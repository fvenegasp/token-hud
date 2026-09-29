import Foundation

/// Lectura tolerante del contrato `state.json` v1 (docs/plans/monitor-cuotas.md §6).
/// Campos desconocidos se ignoran; los opcionales que faltan o vienen con otro tipo quedan en nil.

public enum ISODate {
    /// ISO-8601 con o sin fracciones de segundo.
    public static func parse(_ s: String) -> Date? {
        let plain = ISO8601DateFormatter()
        plain.formatOptions = [.withInternetDateTime]
        if let d = plain.date(from: s) { return d }
        let frac = ISO8601DateFormatter()
        frac.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return frac.date(from: s)
    }
}

/// Envoltorio que nunca falla: un elemento roto se convierte en nil en vez de tumbar todo el archivo.
struct Lossy<T: Decodable>: Decodable {
    let value: T?
    init(from decoder: Decoder) throws {
        value = try? T(from: decoder)
    }
}

extension KeyedDecodingContainer {
    func lenient<T: Decodable>(_ type: T.Type, _ key: Key) -> T? {
        try? decodeIfPresent(type, forKey: key)
    }

    func lenientDate(_ key: Key) -> Date? {
        guard let s = lenient(String.self, key) else { return nil }
        return ISODate.parse(s)
    }
}

public struct QuotaWindow: Decodable, Sendable, Equatable {
    public var kind: String
    public var group: String?
    public var usedPct: Double
    public var resetsAt: Date?
    public var state: String

    enum CodingKeys: String, CodingKey {
        case kind, group, state
        case usedPct = "used_pct"
        case resetsAt = "resets_at"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // kind y used_pct son imprescindibles: sin ellos la ventana se descarta (nunca se inventa un 0 %).
        kind = try c.decode(String.self, forKey: .kind)
        usedPct = try c.decode(Double.self, forKey: .usedPct)
        group = c.lenient(String.self, .group)
        resetsAt = c.lenientDate(.resetsAt)
        state = c.lenient(String.self, .state) ?? "active"
    }
}

public struct PaceEntry: Decodable, Sendable, Equatable {
    public var expectedPct: Double?
    public var ratio: Double?
    public var projectedExhaustAt: Date?
    public var verdict: String?

    enum CodingKeys: String, CodingKey {
        case ratio, verdict
        case expectedPct = "expected_pct"
        case projectedExhaustAt = "projected_exhaust_at"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        expectedPct = c.lenient(Double.self, .expectedPct)
        ratio = c.lenient(Double.self, .ratio)
        projectedExhaustAt = c.lenientDate(.projectedExhaustAt)
        verdict = c.lenient(String.self, .verdict)
    }
}

/// `error` puede llegar como objeto {code, message} (contrato) o como texto suelto.
public struct ProviderError: Decodable, Sendable, Equatable {
    public var code: String?
    public var message: String?

    enum CodingKeys: String, CodingKey { case code, message }

    public init(code: String?, message: String?) {
        self.code = code
        self.message = message
    }

    public init(from decoder: Decoder) throws {
        if let single = try? decoder.singleValueContainer(), let text = try? single.decode(String.self) {
            code = nil
            message = text
            return
        }
        let c = try decoder.container(keyedBy: CodingKeys.self)
        code = c.lenient(String.self, .code)
        message = c.lenient(String.self, .message)
    }
}

public struct ProviderState: Decodable, Sendable, Equatable {
    public var status: String
    public var plan: String?
    public var source: String?
    public var fetchedAt: Date?
    public var error: ProviderError?
    public var note: String?
    public var windows: [QuotaWindow]
    public var pace: [String: PaceEntry]

    enum CodingKeys: String, CodingKey {
        case status, plan, source, error, note, windows, pace
        case fetchedAt = "fetched_at"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        status = c.lenient(String.self, .status) ?? "ok"
        plan = c.lenient(String.self, .plan)
        source = c.lenient(String.self, .source)
        fetchedAt = c.lenientDate(.fetchedAt)
        error = c.lenient(ProviderError.self, .error)
        note = c.lenient(String.self, .note)
        windows = (c.lenient([Lossy<QuotaWindow>].self, .windows) ?? []).compactMap { $0.value }
        pace = (c.lenient([String: Lossy<PaceEntry>].self, .pace) ?? [:]).compactMapValues { $0.value }
    }
}

public struct QuotaState: Decodable, Sendable, Equatable {
    public var schemaVersion: Int
    public var generatedAt: Date?
    public var providers: [String: ProviderState]

    enum CodingKeys: String, CodingKey {
        case providers
        case schemaVersion = "schema_version"
        case generatedAt = "generated_at"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try c.decode(Int.self, forKey: .schemaVersion)
        generatedAt = c.lenientDate(.generatedAt)
        providers = (c.lenient([String: Lossy<ProviderState>].self, .providers) ?? [:]).compactMapValues { $0.value }
    }
}

/// Resultado de leer el archivo de estado.
public enum StateLoad: Sendable, Equatable {
    case state(QuotaState)
    case missing
    case corrupt
    case unsupportedSchema(Int?)
}

public enum StateParser {
    /// Nunca lanza ni se cae: cualquier entrada rara termina en `.corrupt` o `.unsupportedSchema`.
    public static func parse(_ data: Data) -> StateLoad {
        guard let obj = try? JSONSerialization.jsonObject(with: data),
              let dict = obj as? [String: Any]
        else { return .corrupt }
        guard let num = dict["schema_version"] as? NSNumber else { return .unsupportedSchema(nil) }
        guard num.intValue == 1 else { return .unsupportedSchema(num.intValue) }
        do {
            return .state(try JSONDecoder().decode(QuotaState.self, from: data))
        } catch {
            return .corrupt
        }
    }
}

/// Lectura de `~/.config/cuota/env` solo para obtener PATH (nunca se leen credenciales).
public enum EnvFile {
    public static func path(from contents: String, home: String, currentPath: String) -> String? {
        for rawLine in contents.split(whereSeparator: \.isNewline) {
            var line = rawLine.trimmingCharacters(in: .whitespaces)
            if line.isEmpty || line.hasPrefix("#") { continue }
            if line.hasPrefix("export ") { line = String(line.dropFirst(7)).trimmingCharacters(in: .whitespaces) }
            guard let eq = line.firstIndex(of: "=") else { continue }
            let key = line[line.startIndex..<eq].trimmingCharacters(in: .whitespaces)
            guard key == "PATH" else { continue }
            var value = line[line.index(after: eq)...].trimmingCharacters(in: .whitespaces)
            if value.count >= 2, let f = value.first, let l = value.last, f == l, f == "\"" || f == "'" {
                value = String(value.dropFirst().dropLast())
            }
            value = value
                .replacingOccurrences(of: "${HOME}", with: home)
                .replacingOccurrences(of: "$HOME", with: home)
                .replacingOccurrences(of: "${PATH}", with: currentPath)
                .replacingOccurrences(of: "$PATH", with: currentPath)
            if value.hasPrefix("~/") { value = home + String(value.dropFirst(1)) }
            return value.isEmpty ? nil : value
        }
        return nil
    }
}
