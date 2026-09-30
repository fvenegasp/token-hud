import AppKit

/// Dibuja la imagen de cada ítem de la barra: logo + dos líneas apiladas
/// (5 h arriba, semanal/mensual abajo), con insignia "!" si el proveedor está en error.
/// Se dibuja a 2× en un NSBitmapImageRep y se marca como plantilla: la barra de menú
/// lo tiñe sola en claro/oscuro. La opacidad (bloqueado/stale) la pone el botón.
@MainActor
enum StripImageRenderer {
    /// Recurso SVG por proveedor (Kimi no tiene logo oficial: monograma "K").
    private static let logoFile: [String: String] = [
        "claude": "claude",
        "codex": "openai",
        "zai": "zai",
        "agy": "antigravity",
    ]

    static func render(providerID: String, top: String, bottom: String, errorBadge: Bool) -> NSImage {
        let scale: CGFloat = 2
        let barHeight = ceil(NSStatusBar.system.thickness) * scale
        let margin: CGFloat = 1 * scale
        // Una sola línea "5h/semanal" con el tamaño estándar de la barra de menú.
        let font = NSFont.monospacedDigitSystemFont(
            ofSize: NSFont.menuBarFont(ofSize: 0).pointSize * scale, weight: .medium)
        let textAttributes: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: NSColor.black,
        ]
        let textString = "\(top)/\(bottom)" as NSString
        let textSize = textString.size(withAttributes: textAttributes)
        let textWidth = ceil(textSize.width)

        // Todos los logos con la misma altura VISIBLE (~16 pt), acotada a grosor − 4 pt.
        // Escala óptica: los glifos sólidos de borde plano se ven más grandes que los redondos/abiertos.
        let opticalScale: [String: CGFloat] = ["kimi": 0.80, "zai": 0.82, "agy": 0.84]
        let logoHeight: CGFloat = min(16 * scale, barHeight - 4 * scale) * (opticalScale[providerID] ?? 1.0)
        let logo = makeLogo(providerID: providerID, height: logoHeight)
        let logoSide = logo.width
        let gap: CGFloat = 2 * scale
        let badgeDiameter: CGFloat = 8 * scale
        let badgeGap: CGFloat = 2 * scale
        let rightPad: CGFloat = 1 * scale

        var width = logoSide + gap + textWidth + rightPad
        if errorBadge { width += badgeGap + badgeDiameter }
        let pixelWidth = max(1, Int(ceil(width)))
        let pixelHeight = Int(barHeight)

        guard let rep = NSBitmapImageRep(
            bitmapDataPlanes: nil,
            pixelsWide: pixelWidth,
            pixelsHigh: pixelHeight,
            bitsPerSample: 8,
            samplesPerPixel: 4,
            hasAlpha: true,
            isPlanar: false,
            colorSpaceName: .deviceRGB,
            bytesPerRow: 0,
            bitsPerPixel: 0
        ) else { return NSImage() }

        if let context = NSGraphicsContext(bitmapImageRep: rep) {
            NSGraphicsContext.saveGraphicsState()
            NSGraphicsContext.current = context
            context.cgContext.interpolationQuality = .high
            context.shouldAntialias = true

            // Origen abajo-izquierda (el contexto de bitmap no está volteado).
            logo.draw(NSRect(x: 0, y: (barHeight - logoHeight) / 2, width: logoSide, height: logoHeight))

            let textX = logoSide + gap
            drawLine(textString, size: textSize, font: font, attributes: textAttributes,
                     x: textX, width: textWidth, boxBottom: 0, boxHeight: barHeight)

            if errorBadge {
                drawBadge(diameter: badgeDiameter,
                          centerX: width - rightPad - badgeDiameter / 2,
                          centerY: barHeight - badgeDiameter / 2 - margin)
            }

            NSGraphicsContext.restoreGraphicsState()
        }

        let image = NSImage(size: NSSize(width: width / scale, height: barHeight / scale))
        image.addRepresentation(rep)
        image.isTemplate = true
        return image
    }

    /// Logo monocromo del proveedor teñido con `color` (barra lateral: mismo
    /// SVG recortado que la tira, pintado con labelColor en vez de plantilla).
    static func tintedLogo(providerID: String, height: CGFloat, color: NSColor) -> NSImage {
        let scale: CGFloat = 2
        let opticalScale: [String: CGFloat] = ["kimi": 0.80, "zai": 0.82, "agy": 0.84]
        let logoHeight = height * (opticalScale[providerID] ?? 1.0)
        let logo = makeLogo(providerID: providerID, height: logoHeight * scale)
        let pixelWidth = max(1, Int(ceil(logo.width)))

        guard let rep = NSBitmapImageRep(
            bitmapDataPlanes: nil,
            pixelsWide: pixelWidth,
            pixelsHigh: Int(logoHeight * scale),
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0),
              let context = NSGraphicsContext(bitmapImageRep: rep)
        else { return NSImage() }

        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = context
        logo.draw(NSRect(x: 0, y: 0, width: logo.width, height: logoHeight * scale))
        // Teñir respetando la silueta: relleno limitado a los píxeles con alfa.
        color.setStroke()
        color.setFill()
        NSRect(x: 0, y: 0, width: logo.width, height: logoHeight * scale).fill(using: .sourceAtop)
        NSGraphicsContext.restoreGraphicsState()

        let image = NSImage(size: NSSize(width: logo.width / scale, height: logoHeight))
        image.addRepresentation(rep)
        return image
    }

    private static func drawLine(_ string: NSString, size: NSSize, font: NSFont,
                                 attributes: [NSAttributedString.Key: Any],
                                 x: CGFloat, width: CGFloat, boxBottom: CGFloat, boxHeight: CGFloat) {
        // draw(at:) en contexto no volteado: el punto es la esquina inferior-izquierda del renglón
        // (descender). Se centra ascender..descender en la caja.
        let lineHeight = font.ascender - font.descender
        let y = boxBottom + (boxHeight - lineHeight) / 2
        string.draw(at: NSPoint(x: x + (width - size.width), y: y), withAttributes: attributes)
    }

    private struct Logo {
        let width: CGFloat
        let draw: (NSRect) -> Void
    }

    /// Logo con el contenido visible (recorte al bbox de píxeles no transparentes) escalado a `height`.
    private static func makeLogo(providerID: String, height: CGFloat) -> Logo {
        if let file = logoFile[providerID],
           let url = Bundle.module.url(forResource: file, withExtension: "svg", subdirectory: "logos"),
           let logo = NSImage(contentsOf: url), logo.size.width > 0, logo.size.height > 0,
           let trimmed = trimmedBitmap(of: logo) {
            let width = ceil(height * trimmed.size.width / trimmed.size.height)
            return Logo(width: width) { rect in
                trimmed.draw(in: rect, from: .zero, operation: .sourceOver, fraction: 1,
                             respectFlipped: false, hints: [.interpolation: NSImageInterpolation.high])
            }
        }
        // Monograma (Kimi u cualquier logo que falte): la altura de versalitas llena la caja.
        let letter = (providerID == "kimi" ? "K" : String(providerID.prefix(1)).uppercased()) as NSString
        let reference = NSFont.systemFont(ofSize: 100, weight: .bold)
        let font = NSFont.systemFont(ofSize: height * 100 / reference.capHeight, weight: .bold)
        let attributes: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: NSColor.black]
        let size = letter.size(withAttributes: attributes)
        return Logo(width: ceil(size.width)) { rect in
            // Base del renglón = fondo de la caja; las versalitas quedan de borde a borde.
            let baseline = rect.minY
            letter.draw(at: NSPoint(x: rect.minX, y: baseline + font.descender), withAttributes: attributes)
        }
    }

    /// Rasteriza el SVG y recorta al bbox de píxeles con alfa > 0.
    private static func trimmedBitmap(of image: NSImage) -> NSImage? {
        let side = 256
        let aspect = image.size.width / image.size.height
        let w = aspect >= 1 ? side : max(1, Int(CGFloat(side) * aspect))
        let h = aspect >= 1 ? max(1, Int(CGFloat(side) / aspect)) : side
        guard let rep = NSBitmapImageRep(
            bitmapDataPlanes: nil, pixelsWide: w, pixelsHigh: h, bitsPerSample: 8, samplesPerPixel: 4,
            hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0),
              let context = NSGraphicsContext(bitmapImageRep: rep) else { return nil }
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = context
        image.draw(in: NSRect(x: 0, y: 0, width: w, height: h), from: .zero, operation: .sourceOver, fraction: 1)
        NSGraphicsContext.restoreGraphicsState()

        var minX = w, minY = h, maxX = -1, maxY = -1
        for y in 0..<h {
            for x in 0..<w where (rep.colorAt(x: x, y: y)?.alphaComponent ?? 0) > 0.02 {
                minX = min(minX, x); maxX = max(maxX, x); minY = min(minY, y); maxY = max(maxY, y)
            }
        }
        guard maxX >= minX, maxY >= minY, let cg = rep.cgImage,
              let cropped = cg.cropping(to: CGRect(x: minX, y: minY, width: maxX - minX + 1, height: maxY - minY + 1))
        else { return nil }
        return NSImage(cgImage: cropped, size: NSSize(width: cropped.width, height: cropped.height))
    }

    /// Insignia "!": círculo relleno con la marca recortada (plantilla = se adapta a la barra).
    private static func drawBadge(diameter: CGFloat, centerX: CGFloat, centerY: CGFloat) {
        let rect = NSRect(x: centerX - diameter / 2, y: centerY - diameter / 2,
                          width: diameter, height: diameter)
        NSColor.black.setFill()
        NSBezierPath(ovalIn: rect).fill()

        let mark = "!" as NSString
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: diameter * 0.85, weight: .heavy),
        ]
        let size = mark.size(withAttributes: attributes)
        if let context = NSGraphicsContext.current {
            context.cgContext.setBlendMode(.clear)
            mark.draw(at: NSPoint(x: rect.midX - size.width / 2, y: rect.midY - size.height / 2),
                      withAttributes: attributes)
            context.cgContext.setBlendMode(.normal)
        }
    }
}
