import Foundation
import Testing
import CuotaCore

@Suite("Presenter contra el mockup aprobado")
struct PresenterTests {
    // Fixed mockup clock and test time zone.
    static let now = ISODate.parse("2026-09-29T20:55:00Z")!
    static let tz = TimeZone(identifier: "America/Santiago")!

    static func snapshotPresentation() -> Presentation {
        let url = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent() // CuotaCoreTests/
            .deletingLastPathComponent() // Tests/
            .deletingLastPathComponent() // raíz del repo
            .appendingPathComponent("mockup/state-snapshot.json")
        let data = try! Data(contentsOf: url)
        return Presenter.present(StateParser.parse(data), now: now, timeZone: tz)
    }

    static func presentJSON(_ json: String) -> Presentation {
        Presenter.present(StateParser.parse(Data(json.utf8)), now: now, timeZone: tz)
    }

    static func provider(_ presentation: Presentation, _ id: String) -> ProviderPresentation {
        presentation.providers.first { $0.id == id }!
    }

    static func lines(_ provider: ProviderPresentation) -> [String] {
        provider.lines.map { $0.map(\.text).joined() }
    }

    // MARK: - Tira de la barra (valores del snapshot real)

    @Test func stripValues() {
        let p = Self.snapshotPresentation()
        #expect(p.hasData)
        #expect(p.providers.map(\.id) == ["claude", "codex", "kimi", "zai", "agy"])
        #expect(p.providers.map(\.name) == ["Claude", "Codex", "Kimi", "GLM", "Gemini"])
        #expect(Self.provider(p, "claude").stripTop == "68")
        #expect(Self.provider(p, "claude").stripBottom == "70")
        #expect(Self.provider(p, "codex").stripTop == "100")
        #expect(Self.provider(p, "codex").stripBottom == "0")
        #expect(Self.provider(p, "kimi").stripTop == "100")
        #expect(Self.provider(p, "kimi").stripBottom == "78")
        // z.ai: 5 h en reposo → "—" aunque el 0 % de la semanal sí se muestra.
        #expect(Self.provider(p, "zai").stripTop == "—")
        #expect(Self.provider(p, "zai").stripBottom == "0")
        #expect(Self.provider(p, "agy").stripTop == "100")
        #expect(Self.provider(p, "agy").stripBottom == "86")
    }

    // MARK: - Líneas del menú (texto exacto del mockup oscuro)

    @Test func claudeLines() {
        let claude = Self.provider(Self.snapshotPresentation(), "claude")
        #expect(!claude.blocked && !claude.stale && !claude.error)
        #expect(Self.lines(claude) == [
            "5 h: 68 % restante · alcanza",
            "Semanal: 70 % restante · reinicia lun 08:00 · se agota vie 23:05",
            "dato de hace 3 min",
        ])
        #expect(claude.lines[0].last?.role == .ok)
        #expect(claude.lines[1].last?.role == .danger)
        #expect(claude.lines[2].first?.role == .secondary)
    }

    @Test func codexLines() {
        let codex = Self.provider(Self.snapshotPresentation(), "codex")
        #expect(codex.blocked)
        #expect(codex.plan == "Plus")
        #expect(Self.lines(codex) == [
            "5 h: bloqueada hasta sáb 17:23 (3 d 23 h)",
            "Semanal: 0 % restante · vuelve sáb 17:23",
            "dato de hace 2 min",
        ])
        #expect(codex.lines[0].last?.role == .danger)
        #expect(codex.lines[1].last?.role == .danger)
    }

    @Test func kimiLines() {
        let kimi = Self.provider(Self.snapshotPresentation(), "kimi")
        #expect(!kimi.blocked)
        // 7,43 → "7,4" con coma decimal.
        #expect(Self.lines(kimi) == [
            "5 h: 100 % restante · alcanza",
            "Mensual: 78 % restante · reinicia mié 28 oct 21:00 · 7,4× sobre ritmo",
            "dato de hace 2 min",
        ])
        #expect(kimi.lines[1].last?.role == .warn)
    }

    @Test func zaiLines() {
        let zai = Self.provider(Self.snapshotPresentation(), "zai")
        #expect(zai.blocked)
        #expect(zai.idle5h)
        #expect(zai.plan == "Lite")
        #expect(Self.lines(zai) == [
            "5 h: bloqueada hasta hoy 23:07 (5 h 12 min)",
            "Semanal: 0 % restante · vuelve hoy 23:07",
            "dato de hace 2 min",
        ])
    }

    @Test func agyLines() {
        let agy = Self.provider(Self.snapshotPresentation(), "agy")
        #expect(!agy.blocked)
        // Solo cuentan las ventanas del grupo "Gemini Models" y las claves de pace compuestas.
        #expect(Self.lines(agy) == [
            "5 h: 100 % restante · alcanza",
            "Semanal: 86 % restante · reinicia vie 09:15 · alcanza",
            "dato de hace 2 min",
        ])
        #expect(agy.lines[1].last?.role == .ok)
    }

    // MARK: - Reglas de estado (cada una falla si se quita la regla)

    @Test func staleRule() {
        let p = Self.presentJSON("""
        {"schema_version":1,"providers":{"agy":{
          "status":"stale","fetched_at":"2026-09-29T20:08:00Z",
          "windows":[
            {"kind":"5h","group":"Gemini Models","used_pct":10.0,"resets_at":"2026-09-30T01:53:13Z","state":"active"},
            {"kind":"weekly","group":"Gemini Models","used_pct":50.0,"resets_at":"2026-10-02T12:15:36Z","state":"active"}],
          "pace":{}}}}
        """)
        let agy = Self.provider(p, "agy")
        #expect(agy.stale)
        #expect(!agy.blocked && !agy.error)
        #expect(agy.stripTop == "90")
        #expect(Self.lines(agy).last == "dato desactualizado · hace 47 min")
        #expect(agy.lines.last?.first?.role == .warn)
    }

    @Test func errorRule() {
        let p = Self.presentJSON("""
        {"schema_version":1,"providers":{"kimi":{
          "status":"error","error":"HTTP 401","fetched_at":"2026-09-29T20:43:00Z",
          "windows":[
            {"kind":"5h","group":null,"used_pct":12.0,"resets_at":"2026-09-30T01:03:09Z","state":"active"},
            {"kind":"monthly","group":null,"used_pct":22.0,"resets_at":"2026-10-29T00:00:00Z","state":"active"}],
          "pace":{"monthly":{"verdict":"sobre_ritmo","ratio":7.43}}}}}
        """)
        let kimi = Self.provider(p, "kimi")
        #expect(kimi.error)
        #expect(!kimi.stale)
        // Los valores se conservan (la insignia "!" se pone en la app).
        #expect(kimi.stripTop == "88")
        #expect(kimi.stripBottom == "78")
        #expect(Self.lines(kimi).last == "error: HTTP 401 · último valor bueno de hace 12 min")
        #expect(kimi.lines.last?.first?.role == .danger)
    }

    @Test func idleFiveHourLine() {
        let p = Self.presentJSON("""
        {"schema_version":1,"providers":{"zai":{
          "status":"ok","fetched_at":"2026-09-29T20:53:06Z",
          "windows":[
            {"kind":"5h","group":null,"used_pct":0.0,"resets_at":null,"state":"idle"},
            {"kind":"weekly","group":null,"used_pct":40.0,"resets_at":"2026-10-03T20:23:01Z","state":"active"}],
          "pace":{}}}}
        """)
        let zai = Self.provider(p, "zai")
        #expect(zai.idle5h)
        #expect(zai.stripTop == "—")
        #expect(zai.stripBottom == "60")
        #expect(Self.lines(zai).first == "5 h: sin ventana activa")
    }

    @Test func blockedByFiveHourOnlyUsesItsReset() {
        // Solo la 5 h está agotada: la línea 5 h usa su reinicio; la semanal dice "reinicia".
        let p = Self.presentJSON("""
        {"schema_version":1,"providers":{"codex":{
          "status":"ok","fetched_at":"2026-09-29T20:53:06Z",
          "windows":[
            {"kind":"5h","group":null,"used_pct":100.0,"resets_at":"2026-09-30T22:55:00Z","state":"exhausted"},
            {"kind":"weekly","group":null,"used_pct":50.0,"resets_at":"2026-10-05T11:00:00Z","state":"active"}],
          "pace":{}}}}
        """)
        let codex = Self.provider(p, "codex")
        #expect(codex.blocked)
        #expect(!codex.idle5h)
        #expect(Self.lines(codex)[0] == "5 h: bloqueada hasta mañana 19:55 (1 d 2 h)")
        #expect(Self.lines(codex)[1] == "Semanal: 50 % restante · reinicia lun 08:00")
        #expect(codex.lines[1].last?.role == .normal)
    }

    @Test func countdownMinutesOnly() {
        let p = Self.presentJSON("""
        {"schema_version":1,"providers":{"claude":{
          "status":"ok","fetched_at":"2026-09-29T20:53:06Z",
          "windows":[{"kind":"5h","group":null,"used_pct":100.0,"resets_at":"2026-09-29T21:07:00Z","state":"exhausted"}],
          "pace":{}}}}
        """)
        let claude = Self.provider(p, "claude")
        #expect(claude.blocked)
        #expect(claude.stripBottom == "—")
        #expect(Self.lines(claude).first == "5 h: bloqueada hasta hoy 18:07 (12 min)")
    }

    @Test func usedPctAbove100ClampsToZero() {
        let p = Self.presentJSON("""
        {"schema_version":1,"providers":{"claude":{
          "status":"ok","fetched_at":"2026-09-29T20:53:06Z",
          "windows":[{"kind":"5h","group":null,"used_pct":103.2,"resets_at":"2026-09-29T23:00:00Z","state":"active"}],
          "pace":{}}}}
        """)
        #expect(Self.provider(p, "claude").stripTop == "0")
    }

    // MARK: - Estados sin datos

    @Test func unsupportedSchema() {
        let load = StateParser.parse(Data(#"{"schema_version":2,"providers":{}}"#.utf8))
        #expect(load == .unsupportedSchema(2))
        let p = Presenter.present(load, now: Self.now, timeZone: Self.tz)
        #expect(!p.hasData)
        #expect(p.message?.map(\.text).joined() == "Sin datos. ¿Está corriendo cuota? (cuota doctor)")
        #expect(p.providers.count == 5)
        #expect(p.providers.allSatisfy { $0.stripTop == "—" && $0.stripBottom == "—" && $0.lines.isEmpty })
    }

    @Test func corruptJSONDoesNotCrash() {
        let load = StateParser.parse(Data("esto no es json".utf8))
        #expect(load == .corrupt)
        let p = Presenter.present(load, now: Self.now, timeZone: Self.tz)
        #expect(!p.hasData)
        #expect(p.message?.map(\.text).joined() == "Sin datos. ¿Está corriendo cuota? (cuota doctor)")
        #expect(Self.provider(p, "claude").stripTop == "—")
    }

    @Test func missingFile() {
        let p = Presenter.present(.missing, now: Self.now, timeZone: Self.tz)
        #expect(!p.hasData)
        #expect(p.message != nil)
    }

    @Test func missingSchemaVersionIsUnsupported() {
        #expect(StateParser.parse(Data(#"{"providers":{}}"#.utf8)) == .unsupportedSchema(nil))
    }
}
