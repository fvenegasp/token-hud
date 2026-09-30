import AppKit
import CuotaCore

/// Una fila de la barra lateral: logo monocromo, dos renglones "5h" / "sem-mes"
/// con dígitos monoespaciados alineados a la derecha y un medidor del %
/// restante debajo de cada uno. Número, largo y color del medidor cuentan lo
/// mismo (nivel restante); el ritmo es una "▲" ámbar junto a la etiqueta.
/// Mismas reglas de estado que la tira:
/// bloqueada 45 %, desactualizada 72 %, error con "!" y últimos valores buenos.
@MainActor
final class SidebarRowView: NSView {
    private var provider: ProviderPresentation?
    private var logo: NSImage?
    private var secondaryLabel = "sem"
    private var metrics = SidebarSize.normal.metrics
    /// Índice en Presenter.order: la primera fila no lleva filete superior.
    var rowIndex = 0
    var isHovered = false {
        didSet { needsDisplay = true }
    }

    /// Clic (o ⌥-clic) y salida del puntero los resuelve la vista contenedora.
    var onHoverStart: (() -> Void)?
    var onHoverEnd: (() -> Void)?

    override var isFlipped: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        // Los medidores y las cifras comparten número: se recalculan en draw.
        wantsLayer = false
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) no se usa") }

    func applySize(_ size: SidebarSize) {
        metrics = size.metrics
        reloadLogo()
        needsDisplay = true
    }

    func update(provider: ProviderPresentation) {
        self.provider = provider
        secondaryLabel = provider.id == "kimi" ? "mes" : "sem"
        reloadLogo()
        needsDisplay = true
    }

    /// El logo se tiñe con el color de etiqueta vigente (claro/oscuro).
    private func reloadLogo() {
        logo = StripImageRenderer.tintedLogo(providerID: provider?.id ?? "",
                                             height: metrics.logoHeight, color: .labelColor)
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        reloadLogo()
        needsDisplay = true
    }

    // MARK: - Interacción (el menú y el panel los abre el contenedor)

    override func mouseDown(with event: NSEvent) {
        // ⌥-clic abre el panel web; el clic normal abre el menú junto a la barra.
        if event.modifierFlags.contains(.option) {
            (superview as? SidebarContentView)?.openWebPanel()
        } else {
            (superview as? SidebarContentView)?.openMenuFromRow(self)
        }
    }

    override func rightMouseDown(with event: NSEvent) {
        // El menú se abre desde el contenedor, junto a la barra.
        (superview as? SidebarContentView)?.openMenuFromRow(self)
    }

    // MARK: - Hover (retardo de ~300 ms gestionado por el contenedor)

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        trackingAreas.forEach(removeTrackingArea)
        addTrackingArea(NSTrackingArea(rect: bounds, options: [.mouseEnteredAndExited, .activeAlways],
                                       owner: self, userInfo: nil))
    }

    override func mouseEntered(with event: NSEvent) { onHoverStart?() }
    override func mouseExited(with event: NSEvent) { onHoverEnd?() }

    // MARK: - Dibujo

    override func draw(_ dirtyRect: NSRect) {
        guard let provider else { return }
        let context = NSGraphicsContext.current?.cgContext

        if isHovered {
            NSColor.labelColor.withAlphaComponent(0.10).setFill()
            NSBezierPath(roundedRect: bounds.insetBy(dx: 1, dy: 0), xRadius: 6, yRadius: 6).fill()
        }

        // Regla de opacidad de la tira aplicada a toda la fila.
        let alpha: CGFloat = provider.blocked ? 0.45 : (provider.stale ? 0.72 : 1.0)
        context?.setAlpha(alpha)
        defer { context?.setAlpha(1.0) }

        // Filete superior entre filas (la primera no lleva).
        if rowIndex > 0 {
            NSColor.labelColor.withAlphaComponent(0.20).setFill()
            NSRect(x: 5, y: 0, width: bounds.width - 10, height: 0.5).fill()
        }

        let scale = metrics.rowHeight / SidebarMetrics.normal.rowHeight
        let contentX: CGFloat = 9
        let contentW = bounds.width - 18
        let topPadding: CGFloat = 8 * scale
        let logoGap: CGFloat = 6 * scale
        let lineHeight: CGFloat = 18 * scale
        let meterGap: CGFloat = 4 * scale

        // Logo centrado; insignia "!" arriba a la derecha si hay error.
        if let logo {
            let x = bounds.midX - logo.size.width / 2
            logo.draw(in: NSRect(x: x, y: topPadding, width: logo.size.width, height: metrics.logoHeight),
                      from: .zero, operation: .sourceOver, fraction: 1, respectFlipped: true,
                      hints: nil)
        }
        if provider.error {
            drawErrorBadge(x: bounds.width - 17, y: 6)
        }

        var y: CGFloat = topPadding + metrics.logoHeight + logoGap

        y = drawLine(label: "5h", value: provider.stripTop, meter: provider.meterTop,
                     x: contentX, width: contentW, y: y, lineHeight: lineHeight)
        y = drawMeter(provider.meterTop, x: contentX, width: contentW, y: y,
                      gap: meterGap, height: metrics.meterHeight)
        y = drawLine(label: secondaryLabel, value: provider.stripBottom, meter: provider.meterBottom,
                     x: contentX, width: contentW, y: y, lineHeight: lineHeight)
        _ = drawMeter(provider.meterBottom, x: contentX, width: contentW, y: y,
                      gap: meterGap, height: metrics.meterHeight)
    }

    /// Renglón "etiqueta[▲]   cifra %" (cifra a la derecha, dígitos tabulares
    /// tabulares con "%" pequeño en la misma línea base). La cifra se tiñe por
    /// nivel restante; "▲" ámbar avisa que a este ritmo se acaba antes.
    private func drawLine(label: String, value: String, meter: MeterPresentation?,
                          x: CGFloat, width: CGFloat, y: CGFloat, lineHeight: CGFloat) -> CGFloat {
        let valueFont = NSFont.monospacedDigitSystemFont(ofSize: metrics.digitFontSize, weight: .semibold)
        let pctFont = NSFont.systemFont(ofSize: metrics.percentFontSize, weight: .medium)
        let labelFont = NSFont.systemFont(ofSize: metrics.labelFontSize, weight: .medium)

        let labelAttrs: [NSAttributedString.Key: Any] = [.font: labelFont,
                                                         .foregroundColor: NSColor.secondaryLabelColor]
        let labelSize = (label as NSString).size(withAttributes: labelAttrs)
        (label as NSString).draw(
            at: NSPoint(x: x, y: y + (lineHeight - labelSize.height) / 2),
            withAttributes: labelAttrs)

        if meter?.paceWarning == true {
            let mark = "▲" as NSString
            let markAttrs: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: metrics.paceMarkFontSize),
                                                            .foregroundColor: NSColor.systemOrange]
            let markSize = mark.size(withAttributes: markAttrs)
            mark.draw(at: NSPoint(x: x + labelSize.width + 3,
                                  y: y + (lineHeight - markSize.height) / 2),
                      withAttributes: markAttrs)
        }

        // Sin ventana (reposo o ausente): "—" en secundario, sin "%".
        let valueColor = meter.map { PresentationText.color(for: $0.role) } ?? .secondaryLabelColor
        let valueAttrs: [NSAttributedString.Key: Any] = [.font: valueFont, .foregroundColor: valueColor]
        let valueSize = (value as NSString).size(withAttributes: valueAttrs)
        let valueY = y + (lineHeight - valueSize.height) / 2
        var valueX = x + width - valueSize.width
        if meter != nil {
            let pct = "%" as NSString
            let pctAttrs: [NSAttributedString.Key: Any] = [.font: pctFont,
                                                           .foregroundColor: NSColor.secondaryLabelColor]
            let pctSize = pct.size(withAttributes: pctAttrs)
            valueX -= pctSize.width + 1
            // "%" apoyado en la misma línea base que los dígitos.
            pct.draw(at: NSPoint(x: valueX + valueSize.width + 1,
                                 y: valueY + valueFont.ascender - pctFont.ascender),
                     withAttributes: pctAttrs)
        }
        (value as NSString).draw(at: NSPoint(x: valueX, y: valueY), withAttributes: valueAttrs)
        return y + lineHeight
    }

    /// Medidor: pista = 100 % (gris sutil; teñida de rojo si queda 0 %),
    /// relleno = % restante con el color del nivel. Sin ventana → pista vacía.
    private func drawMeter(_ meter: MeterPresentation?, x: CGFloat, width: CGFloat,
                           y: CGFloat, gap: CGFloat, height: CGFloat) -> CGFloat {
        let track = NSRect(x: x, y: y + gap / 2, width: width, height: height)
        let trackColor: NSColor = (meter?.remaining == 0)
            ? NSColor.systemRed.withAlphaComponent(0.25)   // pista teñida cuando queda 0 %
            : NSColor.quaternaryLabelColor
        trackColor.setFill()
        NSBezierPath(roundedRect: track, xRadius: 2, yRadius: 2).fill()

        if let meter {
            let fillW = max(track.width * CGFloat(meter.remaining) / 100, meter.remaining > 0 ? height : 0)
            PresentationText.color(for: meter.role).setFill()
            NSBezierPath(roundedRect: NSRect(x: track.minX, y: track.minY,
                                             width: min(fillW, track.width), height: track.height),
                         xRadius: 2, yRadius: 2).fill()
        }
        return track.maxY + gap
    }

    /// Insignia "!" (roja, con el valor bueno en las cifras).
    private func drawErrorBadge(x: CGFloat, y: CGFloat) {
        let rect = NSRect(x: x, y: y, width: 10, height: 10)
        NSColor.systemRed.setFill()
        NSBezierPath(ovalIn: rect).fill()
        let mark = "!" as NSString
        let font = NSFont.systemFont(ofSize: 8, weight: .heavy)
        let size = mark.size(withAttributes: [.font: font])
        mark.draw(at: NSPoint(x: rect.midX - size.width / 2, y: rect.midY - size.height / 2),
                  withAttributes: [.font: font, .foregroundColor: NSColor.white])
    }
}
