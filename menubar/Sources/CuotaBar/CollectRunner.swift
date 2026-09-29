import Foundation

/// Lanza `~/.local/bin/cuota collect` fuera del hilo principal.
/// El código de salida 75 (EX_TEMPFAIL: ya hay un collect en curso) no es un error.
enum CollectRunner {
    /// `completion` se llama siempre, en una cola de fondo, con el código de salida
    /// (75 incluido) o nil si el proceso no pudo lanzarse.
    static func run(home: String, path: String?, completion: @escaping @Sendable (Int32?) -> Void) {
        let executable = URL(fileURLWithPath: home + "/.local/bin/cuota")
        DispatchQueue.global(qos: .utility).async {
            let process = Process()
            process.executableURL = executable
            process.arguments = ["collect"]
            var environment = ProcessInfo.processInfo.environment
            if let path, !path.isEmpty {
                environment["PATH"] = path
            }
            process.environment = environment
            process.standardOutput = FileHandle.nullDevice
            process.standardError = FileHandle.nullDevice
            do {
                try process.run()
                process.waitUntilExit()
                completion(process.terminationStatus)
            } catch {
                completion(nil)
            }
        }
    }
}
