import Foundation

/// Rol de color de un segmento de texto (lo traduce la app a NSColor).
public enum ColorRole: String, Sendable, Equatable {
    case normal
    case secondary
    case ok      // verde: "alcanza"
    case warn    // ámbar: "N× sobre ritmo", "dato desactualizado"
    case danger  // rojo: "se agota…", "bloqueada…", "vuelve…", errores
}

public struct LineSegment: Sendable, Equatable {
    public var text: String
    public var role: ColorRole

    public init(_ text: String, _ role: ColorRole = .normal) {
        self.text = text
        self.role = role
    }
}

/// Medidor de una ventana para la barra lateral: % restante (mismo número que
/// las cifras) y color según el NIVEL restante (verde > 50, ámbar 20…50,
/// rojo < 20), de modo que número, largo y color cuenten lo mismo. El ritmo va
/// aparte: `paceWarning` marca "a este paso se acaba antes del reinicio".
public struct MeterPresentation: Sendable, Equatable {
    public var remaining: Int
    public var role: ColorRole
    /// Veredicto "se_agota" o "sobre_ritmo" con la ventana aún viva.
    public var paceWarning: Bool

    public init(remaining: Int, role: ColorRole, paceWarning: Bool = false) {
        self.remaining = remaining
        self.role = role
        self.paceWarning = paceWarning
    }
}

/// Presentación de un proveedor: valores de la tira de barra + líneas del menú.
public struct ProviderPresentation: Sendable, Equatable {
    public var id: String
    public var name: String
    public var plan: String?
    public var stripTop: String
    public var stripBottom: String
    /// nil = 5 h en reposo o sin ventana (pista vacía, cifra "—").
    public var meterTop: MeterPresentation?
    public var meterBottom: MeterPresentation?
    public var blocked: Bool
    public var stale: Bool
    public var error: Bool
    public var idle5h: Bool
    public var lines: [[LineSegment]]

    public init(id: String, name: String, plan: String?, stripTop: String, stripBottom: String,
                meterTop: MeterPresentation?, meterBottom: MeterPresentation?,
                blocked: Bool, stale: Bool, error: Bool, idle5h: Bool, lines: [[LineSegment]]) {
        self.id = id
        self.name = name
        self.plan = plan
        self.stripTop = stripTop
        self.stripBottom = stripBottom
        self.meterTop = meterTop
        self.meterBottom = meterBottom
        self.blocked = blocked
        self.stale = stale
        self.error = error
        self.idle5h = idle5h
        self.lines = lines
    }
}

public struct Presentation: Sendable, Equatable {
    public var providers: [ProviderPresentation]
    /// Mensaje cuando no hay estado legible (nil si hay datos).
    public var message: [LineSegment]?
    public var hasData: Bool

    public init(providers: [ProviderPresentation], message: [LineSegment]?, hasData: Bool) {
        self.providers = providers
        self.message = message
        self.hasData = hasData
    }
}

/// Lógica pura de presentación (mismas reglas que el mockup aprobado y el panel web).
/// Sin AppKit: solo Foundation, fechas en la zona horaria dada.
public enum Presenter {
    /// Orden y nombres visibles: claude, codex, kimi, zai → "GLM", agy → "Gemini".
    public static let order: [(id: String, name: String)] = [
        ("claude", "Claude"),
        ("codex", "Codex"),
        ("kimi", "Kimi"),
        ("zai", "GLM"),
        ("agy", "Gemini"),
    ]

    public static let noDataMessage = "Sin datos. ¿Está corriendo cuota? (cuota doctor)"

    /// Grupo de ventanas de agy que muestra la barra (hay más grupos en el estado).
    static let agyGroup = "Gemini Models"

    private static let weekdayAbbr = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"]
    private static let monthAbbr = ["ene", "feb", "mar", "abr", "may", "jun",
                                    "jul", "ago", "sep", "oct", "nov", "dic"]

    public static func present(_ load: StateLoad, now: Date, timeZone: TimeZone) -> Presentation {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        switch load {
        case .state(let state):
            return Presentation(
                providers: order.map { entry in
                    presentProvider(id: entry.id, name: entry.name,
                                    state.providers[entry.id], now: now, calendar: calendar)
                },
                message: nil,
                hasData: true
            )
        case .missing, .corrupt, .unsupportedSchema:
            return Presentation(
                providers: order.map { empty(id: $0.id, name: $0.name) },
                message: [LineSegment(noDataMessage, .secondary)],
                hasData: false
            )
        }
    }

    private static func empty(id: String, name: String) -> ProviderPresentation {
        ProviderPresentation(id: id, name: name, plan: nil, stripTop: "—", stripBottom: "—",
                             meterTop: nil, meterBottom: nil,
                             blocked: false, stale: false, error: false, idle5h: false, lines: [])
    }

    private static func presentProvider(id: String, name: String, _ provider: ProviderState?,
                                        now: Date, calendar: Calendar) -> ProviderPresentation {
        guard let provider else { return empty(id: id, name: name) }

        let second = id == "kimi" ? "monthly" : "weekly"
        let label = second == "monthly" ? "Mensual" : "Semanal"
        // agy trae varias ventanas por kind; solo interesan las del grupo Gemini Models.
        func window(_ kind: String) -> QuotaWindow? {
            provider.windows.first { window in
                window.kind == kind && (id != "agy" || window.group == agyGroup)
            }
        }
        let w5 = window("5h")
        let w2 = window(second)

        let blocked = w2?.state == "exhausted" || w5?.state == "exhausted"
        let stale = provider.status == "stale"
        let error = provider.status == "error"
        let idle5h = w5 == nil || w5?.state == "idle"

        let stripTop: String
        if let w5, w5.state != "idle" {
            stripTop = "\(remaining(w5))"
        } else {
            stripTop = "—"
        }
        let stripBottom = w2.map { "\(remaining($0))" } ?? "—"

        let plan = provider.plan.map { $0.prefix(1).uppercased() + $0.dropFirst() }

        var lines: [[LineSegment]] = []
        if blocked {
            // La ventana agotada manda: si es la semanal/mensual, su reinicio es el que cuenta.
            let culprit = w2?.state == "exhausted" ? w2 : w5
            if let culprit, let resets = culprit.resetsAt {
                lines.append([
                    LineSegment("5 h: "),
                    LineSegment("bloqueada hasta \(when(resets, now: now, calendar: calendar)) (\(countdown(resets.timeIntervalSince(now))))", .danger),
                ])
            }
            if let w2, let resets = w2.resetsAt {
                if w2.state == "exhausted" {
                    lines.append([
                        LineSegment("\(label): \(remaining(w2)) % restante · "),
                        LineSegment("vuelve \(when(resets, now: now, calendar: calendar))", .danger),
                    ])
                } else {
                    lines.append([LineSegment("\(label): \(remaining(w2)) % restante · reinicia \(when(resets, now: now, calendar: calendar))")])
                }
            }
        } else {
            if let w5 {
                if w5.state == "idle" {
                    lines.append([LineSegment("5 h: sin ventana activa")])
                } else {
                    lines.append([LineSegment("5 h: \(remaining(w5)) % restante")]
                        + paceSegments(provider, key: "5h", now: now, calendar: calendar))
                }
            }
            if let w2 {
                var segments = [LineSegment("\(label): \(remaining(w2)) % restante")]
                if let resets = w2.resetsAt {
                    segments.append(LineSegment(" · reinicia \(when(resets, now: now, calendar: calendar))"))
                }
                segments += paceSegments(provider, key: second, now: now, calendar: calendar)
                lines.append(segments)
            }
        }

        if let fetched = provider.fetchedAt {
            if error {
                let detail = provider.error?.message ?? provider.error?.code ?? "desconocido"
                lines.append([LineSegment("error: \(detail) · último valor bueno de hace \(age(fetched, now: now))", .danger)])
            } else if stale {
                lines.append([LineSegment("dato desactualizado · hace \(age(fetched, now: now))", .warn)])
            } else {
                lines.append([LineSegment("dato de hace \(age(fetched, now: now))", .secondary)])
            }
        }

        return ProviderPresentation(id: id, name: name, plan: plan,
                                    stripTop: stripTop, stripBottom: stripBottom,
                                    meterTop: meter(w5, provider: provider, paceKey: "5h"),
                                    meterBottom: meter(w2, provider: provider, paceKey: second),
                                    blocked: blocked, stale: stale, error: error, idle5h: idle5h,
                                    lines: lines)
    }

    /// Medidor de la barra lateral: nil si la ventana no existe o está en reposo.
    /// Color por nivel restante (> 50 verde, 20…50 ámbar, < 20 rojo; agotada → 0
    /// rojo). El aviso de ritmo ("se_agota"/"sobre_ritmo") viaja aparte y no tiñe.
    private static func meter(_ window: QuotaWindow?, provider: ProviderState,
                              paceKey: String) -> MeterPresentation? {
        guard let window, window.state != "idle" else { return nil }
        let rem = remaining(window)
        let role: ColorRole = rem > 50 ? .ok : (rem >= 20 ? .warn : .danger)
        let verdict = paceEntry(provider, key: paceKey)?.verdict
        let paceWarning = window.state != "exhausted"
            && (verdict == "se_agota" || verdict == "sobre_ritmo")
        return MeterPresentation(remaining: rem, role: role, paceWarning: paceWarning)
    }

    /// Entrada de pace por clave exacta o compuesta ("weekly:Gemini Models").
    private static func paceEntry(_ provider: ProviderState, key: String) -> PaceEntry? {
        provider.pace.first(where: { $0.key == key || $0.key.hasPrefix(key + ":") })?.value
    }

    /// Segmento de ritmo: verde "alcanza", ámbar "N× sobre ritmo", rojo "se agota …".
    /// La clave de pace puede venir compuesta ("weekly:Gemini Models").
    private static func paceSegments(_ provider: ProviderState, key: String,
                                     now: Date, calendar: Calendar) -> [LineSegment] {
        guard let entry = paceEntry(provider, key: key),
              let verdict = entry.verdict
        else { return [] }
        switch verdict {
        case "se_agota":
            guard let exhaust = entry.projectedExhaustAt else { return [] }
            return [LineSegment(" · "), LineSegment("se agota \(when(exhaust, now: now, calendar: calendar))", .danger)]
        case "sobre_ritmo":
            guard let ratio = entry.ratio else { return [] }
            let text = String(format: "%.1f", ratio).replacingOccurrences(of: ".", with: ",")
            return [LineSegment(" · "), LineSegment("\(text)× sobre ritmo", .warn)]
        case "bajo_ritmo":
            return [LineSegment(" · "), LineSegment("alcanza", .ok)]
        default:
            return []
        }
    }

    // MARK: - Formato (idéntico al mockup)

    /// % restante entero (redondeo al más cercano, mínimo 0).
    private static func remaining(_ window: QuotaWindow) -> Int {
        max(0, Int((100 - window.usedPct).rounded()))
    }

    private static func hourMinute(_ date: Date, calendar: Calendar) -> String {
        let parts = calendar.dateComponents([.hour, .minute], from: date)
        return String(format: "%02d:%02d", parts.hour ?? 0, parts.minute ?? 0)
    }

    /// "hoy 20:00", "mañana 08:00", "sáb 17:23" (< 7 días) o "mié 28 oct 21:00".
    private static func when(_ date: Date, now: Date, calendar: Calendar) -> String {
        let time = hourMinute(date, calendar: calendar)
        if calendar.isDate(date, inSameDayAs: now) { return "hoy \(time)" }
        if calendar.isDate(date, inSameDayAs: now.addingTimeInterval(86400)) { return "mañana \(time)" }
        let weekday = weekdayAbbr[calendar.component(.weekday, from: date) - 1]
        if abs(date.timeIntervalSince(now)) < 7 * 86400 {
            return "\(weekday) \(time)"
        }
        let day = calendar.component(.day, from: date)
        let month = monthAbbr[calendar.component(.month, from: date) - 1]
        return "\(weekday) \(day) \(month) \(time)"
    }

    /// "(3 d 23 h)" / "(5 h 12 min)" / "(12 min)".
    private static func countdown(_ interval: TimeInterval) -> String {
        let minutes = Int((interval / 60).rounded())
        let days = minutes / 1440
        let hours = (minutes % 1440) / 60
        let mins = minutes % 60
        if days > 0 { return "\(days) d \(hours) h" }
        if hours > 0 { return "\(hours) h \(mins) min" }
        return "\(mins) min"
    }

    /// "3 min" o "2 h 5 min" (nunca negativo).
    private static func age(_ date: Date, now: Date) -> String {
        let minutes = max(0, Int((now.timeIntervalSince(date) / 60).rounded()))
        if minutes < 60 { return "\(minutes) min" }
        return "\(minutes / 60) h \(minutes % 60) min"
    }
}
