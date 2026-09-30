import Foundation

/// Modo de visualización excluyente de Token HUD.
enum DisplayMode: String {
    case menuBar    // un NSStatusItem por proveedor (comportamiento histórico)
    case sidebar    // barra lateral flotante, sin ítems en la barra de menú
}

/// Borde de la pantalla al que se ancla la barra lateral.
enum SidebarEdge: String {
    case right
    case left
    case top
    case bottom

    /// Superior / inferior: la barra corre en horizontal a lo largo del borde.
    var isHorizontal: Bool { self == .top || self == .bottom }
}

struct SidebarMetrics {
    let panelWidth: CGFloat
    /// Alto de la barra en orientación horizontal (bordes superior / inferior).
    let panelHeight: CGFloat
    let logoHeight: CGFloat
    let digitFontSize: CGFloat
    let percentFontSize: CGFloat
    let labelFontSize: CGFloat
    let paceMarkFontSize: CGFloat
    let meterHeight: CGFloat
    let rowHeight: CGFloat

    static let compact = SidebarMetrics(
        panelWidth: 64, panelHeight: 43, logoHeight: 14, digitFontSize: 12, percentFontSize: 8,
        labelFontSize: 9, paceMarkFontSize: 7, meterHeight: 3, rowHeight: 73
    )
    static let normal = SidebarMetrics(
        panelWidth: 84, panelHeight: 56, logoHeight: 18, digitFontSize: 15, percentFontSize: 10,
        labelFontSize: 11, paceMarkFontSize: 9, meterHeight: 4, rowHeight: 96
    )
    static let large = SidebarMetrics(
        panelWidth: 108, panelHeight: 72, logoHeight: 22, digitFontSize: 19, percentFontSize: 12,
        labelFontSize: 13, paceMarkFontSize: 11, meterHeight: 5, rowHeight: 123
    )
}

/// Tamaño visual de la barra lateral.
enum SidebarSize: String {
    case compact
    case normal
    case large

    var metrics: SidebarMetrics {
        switch self {
        case .compact: .compact
        case .normal: .normal
        case .large: .large
        }
    }
}

/// Preferencias persistidas en UserDefaults. Por defecto "Barra de menú",
/// para que los usuarios actuales no vean ningún cambio.
@MainActor
final class DisplayModeSettings {
    static let shared = DisplayModeSettings()

    private let defaults: UserDefaults
    private static let modeKey = "displayMode"
    private static let edgeKey = "sidebarEdge"
    private static let sizeKey = "sidebarSize"
    /// Posición vertical como fracción de la altura visible (0 = arriba, 1 = abajo).
    private static let positionKey = "sidebarPosition"
    /// Posición horizontal como fracción del ancho visible (0 = izquierda, 1 = derecha).
    /// Se guarda aparte: cada orientación recuerda su propia posición.
    private static let positionXKey = "sidebarPositionX"

    /// Se llama al cambiar cualquier preferencia (el coordinador reacciona).
    var onChange: (() -> Void)?

    private init() {
        defaults = .standard
    }

    var mode: DisplayMode {
        get { DisplayMode(rawValue: defaults.string(forKey: Self.modeKey) ?? "") ?? .menuBar }
        set {
            guard newValue != mode else { return }
            defaults.set(newValue.rawValue, forKey: Self.modeKey)
            onChange?()
        }
    }

    var edge: SidebarEdge {
        get { SidebarEdge(rawValue: defaults.string(forKey: Self.edgeKey) ?? "") ?? .right }
        set {
            guard newValue != edge else { return }
            defaults.set(newValue.rawValue, forKey: Self.edgeKey)
            onChange?()
        }
    }

    var sidebarSize: SidebarSize {
        get { SidebarSize(rawValue: defaults.string(forKey: Self.sizeKey) ?? "") ?? .normal }
        set {
            guard newValue != sidebarSize else { return }
            defaults.set(newValue.rawValue, forKey: Self.sizeKey)
            onChange?()
        }
    }

    var verticalFraction: Double {
        get {
            let value = defaults.double(forKey: Self.positionKey)
            // 0.0 es "sin valor guardado": centrado (0.5).
            return value == 0.0 ? 0.5 : min(1, max(0, value))
        }
        set {
            defaults.set(min(1, max(0, newValue)), forKey: Self.positionKey)
        }
    }

    var horizontalFraction: Double {
        get {
            let value = defaults.double(forKey: Self.positionXKey)
            // 0.0 es "sin valor guardado": centrado (0.5).
            return value == 0.0 ? 0.5 : min(1, max(0, value))
        }
        set {
            defaults.set(min(1, max(0, newValue)), forKey: Self.positionXKey)
        }
    }
}
