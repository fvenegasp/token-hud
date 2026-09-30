import AppKit
import CuotaCore

/// Barra lateral flotante: NSPanel no activante (nunca roba el foco), anclada
/// a un borde de la pantalla con la barra de menú (NSScreen.screens.first) —
/// vertical en derecho / izquierdo, horizontal en superior / inferior —
/// con material translúcido, esquinas de 12 pt, borde de 0,5 px y sombra suave.
@MainActor
final class SidebarPanel: NSPanel {
    static let inset: CGFloat = 6

    let settings = DisplayModeSettings.shared

    override var canBecomeKey: Bool { false }

    init() {
        let metrics = settings.sidebarSize.metrics
        super.init(contentRect: NSRect(x: 0, y: 0, width: metrics.panelWidth, height: 100),
                   styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        isFloatingPanel = true
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary]
        isOpaque = false
        backgroundColor = .clear
        hasShadow = true
        hidesOnDeactivate = false
        isMovableByWindowBackground = false
        becomesKeyOnlyIfNeeded = true
        ignoresMouseEvents = false
        animationBehavior = .utilityWindow

        contentView = SidebarContentView(frame: NSRect(x: 0, y: 0, width: metrics.panelWidth, height: 100))

        // Re-anclar si cambia la resolución o la pantalla principal.
        NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.applyPosition() }
        }
    }

    // MARK: - Anclaje (borde + fracción de la pantalla en el eje del borde)

    /// Recoloca el panel según borde y fracción persistidas, con centrado por defecto.
    func applyPosition() {
        guard let visible = NSScreen.screens.first?.visibleFrame else { return }
        if settings.edge.isHorizontal {
            let metrics = settings.sidebarSize.metrics
            let height = metrics.panelHeight
            // El ancho crece con las celdas, pero nunca más que el marco visible.
            let width = min(contentView?.frame.width ?? 0, visible.width)
            var frame = NSRect(x: 0, y: 0, width: width, height: height)

            // Fracción 0 = izquierda, 1 = derecha; el centro del panel sigue la fracción.
            var centerX = visible.minX + visible.width * settings.horizontalFraction
            centerX = min(max(centerX, visible.minX + width / 2), visible.maxX - width / 2)
            frame.origin.x = centerX - width / 2
            // A 6 pt del marco visible: bajo la barra de menú arriba; sobre el Dock
            // abajo (o junto al borde si el Dock no ocupa ese lado).
            frame.origin.y = settings.edge == .top ? visible.maxY - height - Self.inset
                                                   : visible.minY + Self.inset
            setFrame(frame, display: true)
            return
        }

        let width = settings.sidebarSize.metrics.panelWidth
        // El alto crece con las filas, pero nunca más que el marco visible.
        let height = min(contentView?.frame.height ?? 0, visible.height)
        var frame = NSRect(x: 0, y: 0, width: width, height: height)

        // Fracción 0 = arriba, 1 = abajo; el centro del panel sigue la fracción.
        var centerY = visible.maxY - visible.height * settings.verticalFraction
        centerY = min(max(centerY, visible.minY + height / 2), visible.maxY - height / 2)
        frame.origin.y = centerY - height / 2
        frame.origin.x = settings.edge == .right ? visible.maxX - width - Self.inset
                                                 : visible.minX + Self.inset
        setFrame(frame, display: true)
    }

    /// Fija la posición vertical durante el arrastre del asa (solo a lo largo del borde).
    func drag(to screenY: CGFloat) {
        guard let visible = NSScreen.screens.first?.visibleFrame else { return }
        var frame = self.frame
        let minY = visible.minY
        let maxY = visible.maxY - frame.height
        frame.origin.y = min(max(screenY, minY), maxY)
        setFrameOrigin(frame.origin)
    }

    /// Fija la posición horizontal durante el arrastre del asa (borde superior / inferior).
    func dragHorizontally(to screenX: CGFloat) {
        guard let visible = NSScreen.screens.first?.visibleFrame else { return }
        var frame = self.frame
        let minX = visible.minX
        let maxX = visible.maxX - frame.width
        frame.origin.x = min(max(screenX, minX), maxX)
        setFrameOrigin(frame.origin)
    }

    /// Persiste la posición actual como fracción de la pantalla, en la clave de
    /// la orientación vigente (cada orientación recuerda la suya).
    func persistPosition() {
        guard let visible = NSScreen.screens.first?.visibleFrame else { return }
        if settings.edge.isHorizontal {
            settings.horizontalFraction = (frame.midX - visible.minX) / visible.width
        } else {
            settings.verticalFraction = (visible.maxY - frame.midY) / visible.height
        }
    }

    var edge: SidebarEdge { settings.edge }

    func applySize() {
        guard let content = contentView as? SidebarContentView else { return }
        content.applySize(settings.sidebarSize, horizontal: settings.edge.isHorizontal)
        applyPosition()
    }

    // MARK: - Datos

    func update(_ presentation: Presentation) {
        guard let content = contentView as? SidebarContentView else { return }
        content.applySize(settings.sidebarSize, horizontal: settings.edge.isHorizontal)
        content.update(presentation)
        applyPosition()
    }
}

/// Contenido de la barra: asa + una fila por proveedor en Presenter.order.
/// Vertical: asa arriba y filas apiladas. Horizontal: asa a la izquierda y
/// celdas lado a lado, separadas por filetes verticales.
@MainActor
final class SidebarContentView: NSView {
    private let grip = SidebarGripView()
    private var rows: [SidebarRowView] = []
    private var hoverTimer: Timer?
    private let popover = NSPopover()

    /// El menú compartido y el panel web los aporta el coordinador.
    weak var delegate: SidebarDelegate?
    private var size = SidebarSize.normal
    /// true = barra en borde superior / inferior (celdas en horizontal).
    private var horizontal = false

    override var isFlipped: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        layer?.cornerRadius = 12
        layer?.masksToBounds = true
        applyHairline()

        let effect = NSVisualEffectView(frame: bounds)
        effect.material = .hudWindow
        effect.blendingMode = .behindWindow
        effect.state = .active
        effect.autoresizingMask = [.width, .height]
        // El filete de 0,5 px va en el borde de la capa del contenedor.
        addSubview(effect)

        addSubview(grip)

        popover.behavior = .transient
        popover.contentViewController = ProviderBlockViewController()
        relayout()
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) no se usa") }

    /// Aplica tamaño y orientación; reorienta y redisponde al cambiar de borde.
    func applySize(_ newSize: SidebarSize, horizontal newHorizontal: Bool) {
        guard newSize != size || newHorizontal != horizontal else { return }
        size = newSize
        horizontal = newHorizontal
        grip.horizontal = newHorizontal
        rows.forEach { $0.applySize(newSize); $0.horizontal = newHorizontal }
        relayout()
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        // El filete sigue claro/oscuro del sistema.
        applyHairline()
    }

    /// Filete de 0,5 px alrededor de la barra.
    private func applyHairline() {
        layer?.borderWidth = 0.5
        layer?.borderColor = NSColor.labelColor.withAlphaComponent(0.25).cgColor
    }

    // MARK: - Refresco (misma Presentation que la barra de menú)

    func update(_ presentation: Presentation) {
        applySize(size, horizontal: horizontal)
        if rows.isEmpty {
            for (index, entry) in Presenter.order.enumerated() {
                let row = SidebarRowView()
                row.rowIndex = index
                row.horizontal = horizontal
                row.onHoverStart = { [weak self, weak row] in self?.hoverBegan(on: row, id: entry.id) }
                row.onHoverEnd = { [weak self] in self?.hoverEnded() }
                addSubview(row)
                rows.append(row)
            }
            relayout()
        }
        for (index, provider) in presentation.providers.enumerated() {
            guard index < rows.count else { continue }
            rows[index].update(provider: provider)
        }
        // El ancho de cada celda depende del contenido: redistribuir con datos frescos.
        if horizontal { relayout() }
    }

    private func relayout() {
        let metrics = size.metrics
        if horizontal {
            let height = metrics.panelHeight
            grip.frame = NSRect(x: 3, y: 3, width: 16, height: height - 6)
            var x = grip.frame.maxX
            for row in rows {
                let width = row.preferredCellWidth()
                row.frame = NSRect(x: x, y: 3, width: width, height: height - 6)
                x += width
            }
            setFrameSize(NSSize(width: x + 3, height: height))
            return
        }
        grip.frame = NSRect(x: 3, y: 4, width: metrics.panelWidth - 6, height: 12)
        var y = grip.frame.maxY + 2
        for row in rows {
            row.frame = NSRect(x: 3, y: y, width: metrics.panelWidth - 6,
                               height: metrics.rowHeight)
            y += metrics.rowHeight
        }
        setFrameSize(NSSize(width: metrics.panelWidth, height: y + 4))
    }

    // MARK: - Globo de detalle (~300 ms de hover; mismo bloque que el menú)

    private func hoverBegan(on row: SidebarRowView?, id: String) {
        hoverTimer?.invalidate()
        guard let row else { return }
        hoverTimer = Timer.scheduledTimer(withTimeInterval: 0.3, repeats: false) { [weak self] _ in
            MainActor.assumeIsolated { self?.showPopover(for: row, id: id) }
        }
    }

    private func hoverEnded() {
        hoverTimer?.invalidate()
        hoverTimer = nil
        popover.performClose(nil)
    }

    private func showPopover(for row: SidebarRowView, id: String) {
        guard let presentation = delegate?.presentation(for: id) else { return }
        (popover.contentViewController as? ProviderBlockViewController)?.show(presentation)
        // Hacia el centro de la pantalla: a la izquierda si la barra está a la
        // derecha; debajo de la barra en el borde superior, encima en el inferior.
        let edge = (window as? SidebarPanel)?.edge ?? .right
        let preferredEdge: NSRectEdge
        switch edge {
        case .left: preferredEdge = .maxX
        case .right: preferredEdge = .minX
        case .top: preferredEdge = .minY
        case .bottom: preferredEdge = .maxY
        }
        let anchor = horizontal ? row.bounds.insetBy(dx: -2, dy: 0)
                                : row.bounds.insetBy(dx: 0, dy: -2)
        popover.show(relativeTo: anchor, of: row, preferredEdge: preferredEdge)
    }

    // MARK: - Menú y panel web (acciones del coordinador)

    func openMenuFromRow(_ row: SidebarRowView) {
        hoverEnded()
        delegate?.openMenu(in: self, at: menuAnchor(for: row))
    }

    func openMenuFromGrip() {
        hoverEnded()
        delegate?.openMenu(in: self, at: menuAnchor(for: grip))
    }

    /// Punto de anclaje del menú, en el borde de la barra que mira al centro de
    /// la pantalla: el coordinador lo despliega hacia adentro.
    private func menuAnchor(for view: NSView) -> NSPoint {
        let edge = (window as? SidebarPanel)?.edge ?? .right
        switch edge {
        case .top:
            return NSPoint(x: view.frame.midX, y: bounds.height)
        case .bottom:
            return NSPoint(x: view.frame.midX, y: 0)
        case .right, .left:
            return NSPoint(x: bounds.width / 2, y: view.frame.midY)
        }
    }

    func openWebPanel() {
        delegate?.openWebPanel()
    }
}

/// Contrato con el coordinador (StatusBarController): mismo menú, mismos datos.
@MainActor
protocol SidebarDelegate: AnyObject {
    func openMenu(in view: NSView, at point: NSPoint)
    func openWebPanel()
    func presentation(for providerID: String) -> ProviderPresentation?
}

/// Asa de la barra: arrastrar la mueve a lo largo de su borde; un clic (o clic
/// secundario) abre el menú. Horizontal (cápsula de 20 × 4) arriba en la barra
/// vertical; vertical (4 × 20) al extremo izquierdo en la barra horizontal.
@MainActor
final class SidebarGripView: NSView {
    override var isFlipped: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    /// true = barra en borde superior / inferior: cápsula vertical, arrastre en X.
    var horizontal = false {
        didSet { needsDisplay = true }
    }

    private var dragStart: NSPoint?
    private var originStart: NSPoint?
    private var dragged = false

    override func draw(_ dirtyRect: NSRect) {
        let rect = horizontal
            ? NSRect(x: bounds.midX - 2, y: bounds.midY - 10, width: 4, height: 20)
            : NSRect(x: bounds.midX - 10, y: bounds.midY - 2, width: 20, height: 4)
        NSColor.labelColor.withAlphaComponent(0.35).setFill()
        NSBezierPath(roundedRect: rect, xRadius: 2, yRadius: 2).fill()
    }

    override func mouseDown(with event: NSEvent) {
        dragged = false
        dragStart = NSEvent.mouseLocation
        originStart = window?.frame.origin
        // Bucle de arrastre local (la barra no es clave, pero sí recibe eventos).
        while let next = window?.nextEvent(matching: [.leftMouseDragged, .leftMouseUp]) {
            if next.type == .leftMouseUp { break }
            dragged = true
            guard let start = dragStart, let origin = originStart else { continue }
            let now = NSEvent.mouseLocation
            if horizontal {
                (window as? SidebarPanel)?.dragHorizontally(to: origin.x + now.x - start.x)
            } else {
                (window as? SidebarPanel)?.drag(to: origin.y + now.y - start.y)
            }
        }
        if dragged {
            (window as? SidebarPanel)?.persistPosition()
        } else {
            (superview as? SidebarContentView)?.openMenuFromGrip()
        }
    }

    override func rightMouseDown(with event: NSEvent) {
        (superview as? SidebarContentView)?.openMenuFromGrip()
    }
}

/// Globo de detalle: el mismo bloque de proveedor que el menú desplegable.
@MainActor
final class ProviderBlockViewController: NSViewController {
    private let effect = NSVisualEffectView()
    private let textField = NSTextField(labelWithString: "")

    init() {
        super.init(nibName: nil, bundle: nil)
        effect.material = .popover
        effect.blendingMode = .behindWindow
        effect.state = .active

        textField.isEditable = false
        textField.isBordered = false
        textField.drawsBackground = false
        textField.lineBreakMode = .byWordWrapping
        textField.maximumNumberOfLines = 0
        textField.allowsDefaultTighteningForTruncation = false

        view = effect

        textField.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(textField)
        NSLayoutConstraint.activate([
            textField.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 12),
            textField.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -12),
            textField.topAnchor.constraint(equalTo: view.topAnchor, constant: 10),
            textField.bottomAnchor.constraint(equalTo: view.bottomAnchor, constant: -10),
            view.widthAnchor.constraint(equalToConstant: 280),
        ])
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) no se usa") }

    func show(_ provider: ProviderPresentation) {
        let text = NSMutableAttributedString(attributedString: PresentationText.header(for: provider))
        let font = NSFont.menuFont(ofSize: 12)
        for line in provider.lines {
            text.append(NSAttributedString(string: "\n"))
            text.append(PresentationText.line(line, font: font))
        }
        if text.length == 0 {
            text.append(NSAttributedString(string: "Sin datos",
                                           attributes: [.font: font, .foregroundColor: NSColor.secondaryLabelColor]))
        }
        textField.attributedStringValue = text
    }
}
