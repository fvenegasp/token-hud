import AppKit
import CuotaCore

/// Texto y colores compartidos: el menú desplegable y el globo de la barra
/// lateral muestran el mismo bloque por proveedor.
@MainActor
enum PresentationText {
    static func color(for role: ColorRole) -> NSColor {
        switch role {
        case .normal: return .labelColor
        case .secondary: return .secondaryLabelColor
        case .ok: return .systemGreen
        case .warn: return .systemOrange
        case .danger: return .systemRed
        }
    }

    /// Encabezado "Nombre · Plan" (plan en secundario).
    static func header(for provider: ProviderPresentation) -> NSAttributedString {
        let header = NSMutableAttributedString(
            string: provider.name,
            attributes: [
                .font: NSFont.boldSystemFont(ofSize: 13),
                .foregroundColor: provider.blocked ? NSColor.secondaryLabelColor : NSColor.labelColor,
            ])
        if let plan = provider.plan {
            header.append(NSAttributedString(
                string: " · \(plan)",
                attributes: [
                    .font: NSFont.systemFont(ofSize: 13),
                    .foregroundColor: NSColor.secondaryLabelColor,
                ]))
        }
        return header
    }

    /// Una línea del bloque con los colores de sus segmentos.
    static func line(_ segments: [LineSegment], font: NSFont) -> NSAttributedString {
        let result = NSMutableAttributedString()
        for segment in segments {
            result.append(NSAttributedString(
                string: segment.text,
                attributes: [.font: font, .foregroundColor: color(for: segment.role)]))
        }
        return result
    }
}
