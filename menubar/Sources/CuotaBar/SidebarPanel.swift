import AppKit
import CuotaCore

/// Barra lateral flotante: NSPanel no activante (nunca roba el foco), anclada
/// al borde derecho o izquierdo de la pantalla con la barra de menú
/// (NSScreen.screens.first),
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

    // MARK: - Anclaje (borde + fracción vertical de la pantalla)

    /// Recoloca el panel según borde y fracción persistidas, con centrado por defecto.
    func applyPosition() {
        guard let visible = NSScreen.screens.first?.visibleFrame else { return }
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

    /// Persiste la posición vertical actual como fracción de la pantalla.
    func persistVerticalPosition() {
        guard let visible = NSScreen.screens.first?.visibleFrame else { return }
        settings.verticalFraction = (visible.maxY - frame.midY) / visible.height
    }

    var edge: SidebarEdge { settings.edge }

    func applySize() {
        guard let content = contentView as? SidebarContentView else { return }
        content.applySize(settings.sidebarSize)
        applyPosition()
    }

    // MARK: - Datos

    func update(_ presentation: Presentation) {
        guard let content = contentView as? SidebarContentView else { return }
        content.applySize(settings.sidebarSize)
        content.update(presentation)
        applyPosition()
    }
}

/// Contenido de la barra: asa arriba + una fila por proveedor en Presenter.order.
@MainActor
final class SidebarContentView: NSView {
    private let grip = SidebarGripView()
    private var rows: [SidebarRowView] = []
    private var hoverTimer: Timer?
    private let popover = NSPopover()

    /// El menú compartido y el panel web los aporta el coordinador.
    weak var delegate: SidebarDelegate?
    private var size = SidebarSize.normal

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

        grip.frame = NSRect(x: 3, y: 4, width: frameRect.width - 6, height: 12)
        addSubview(grip)

        popover.behavior = .transient
        popover.contentViewController = ProviderBlockViewController()
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) no se usa") }

    func applySize(_ newSize: SidebarSize) {
        guard newSize != size else { return }
        size = newSize
        let metrics = newSize.metrics
        grip.frame = NSRect(x: 3, y: 4, width: metrics.panelWidth - 6, height: 12)
        rows.forEach { $0.applySize(newSize) }
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
        applySize(size)
        if rows.isEmpty {
            for (index, entry) in Presenter.order.enumerated() {
                let row = SidebarRowView()
                row.rowIndex = index
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
    }

    private func relayout() {
        let metrics = size.metrics
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
        // Hacia el centro de la pantalla: izquierda si la barra está a la derecha.
        popover.show(relativeTo: row.bounds.insetBy(dx: 0, dy: -2), of: row,
                     preferredEdge: (window as? SidebarPanel)?.edge == .left ? .maxX : .minX)
    }

    // MARK: - Menú y panel web (acciones del coordinador)

    func openMenuFromRow(_ row: SidebarRowView) {
        hoverEnded()
        delegate?.openMenu(in: self, at: NSPoint(x: bounds.width / 2, y: row.frame.midY))
    }

    func openMenuFromGrip() {
        hoverEnded()
        delegate?.openMenu(in: self, at: NSPoint(x: bounds.width / 2, y: grip.frame.midY))
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

/// Asa superior: arrastrar mueve la barra a lo largo del borde; un clic (o clic
/// secundario) abre el menú.
@MainActor
final class SidebarGripView: NSView {
    override var isFlipped: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    private var dragStart: NSPoint?
    private var originStart: NSPoint?
    private var dragged = false

    override func draw(_ dirtyRect: NSRect) {
        // Cápsula de 20 × 4 centrada.
        let rect = NSRect(x: bounds.midX - 10, y: bounds.midY - 2, width: 20, height: 4)
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
            (window as? SidebarPanel)?.drag(to: origin.y + now.y - start.y)
        }
        if dragged {
            (window as? SidebarPanel)?.persistVerticalPosition()
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
