import AppKit
import Darwin
import CuotaCore

/// Un NSStatusItem por proveedor, un menú compartido que se reconstruye en cada refresco.
/// Datos: ~/.cache/cuota/state.json (vigilado por rename atómico + timer de respaldo).
@MainActor
final class StatusBarController {
    private var statusItems: [String: NSStatusItem] = [:]
    private let menu = NSMenu()
    private var presentation: Presentation?
    private var lastLoad: StateLoad = .missing

    private var watchSource: DispatchSourceFileSystemObject?
    private var pendingReload: DispatchWorkItem?
    private var fallbackTimer: Timer?
    private var textTimer: Timer?
    private var collectRunning = false

    private var home: String { NSHomeDirectory() }
    private var statePath: String { home + "/.cache/cuota/state.json" }
    private var stateDirectory: String { home + "/.cache/cuota" }
    private var envFilePath: String { home + "/.config/cuota/env" }

    func start() {
        menu.autoenablesItems = false
        // NSStatusBar coloca cada ítem nuevo a la IZQUIERDA del anterior: crear en orden inverso.
        for entry in Presenter.order.reversed() {
            let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
            item.menu = menu
            statusItems[entry.id] = item
        }
        reload()
        startWatching()
        // Respaldo por si el watch pierde un evento; y textos relativos ("hace N min") frescos.
        fallbackTimer = Timer.scheduledTimer(timeInterval: 60, target: self,
                                             selector: #selector(fallbackTimerFired), userInfo: nil, repeats: true)
        textTimer = Timer.scheduledTimer(timeInterval: 30, target: self,
                                         selector: #selector(textTimerFired), userInfo: nil, repeats: true)
    }

    /// QA: escribe <dir>/<provider>.png (2×) con el state.json actual, sin crear ítems.
    func renderStrips(to directory: String) {
        let load: StateLoad
        if let data = FileManager.default.contents(atPath: statePath) { load = StateParser.parse(data) } else { load = .missing }
        try? FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: true)
        let presentation = Presenter.present(load, now: Date(), timeZone: .current)
        for provider in presentation.providers {
            let image = StripImageRenderer.render(providerID: provider.id, top: provider.stripTop,
                                                  bottom: provider.stripBottom, errorBadge: provider.error)
            guard let rep = image.representations.first as? NSBitmapImageRep,
                  let png = rep.representation(using: .png, properties: [:]) else { continue }
            try? png.write(to: URL(fileURLWithPath: directory + "/\(provider.id).png"))
        }
    }

    func stop() {
        fallbackTimer?.invalidate()
        textTimer?.invalidate()
        watchSource?.cancel()
        watchSource = nil
    }

    // MARK: - Datos

    private func reload() {
        if let data = FileManager.default.contents(atPath: statePath) {
            lastLoad = StateParser.parse(data)
        } else {
            lastLoad = .missing
        }
        render()
    }

    private func render() {
        presentation = Presenter.present(lastLoad, now: Date(), timeZone: .current)
        applyPresentation()
        rebuildMenu()
    }

    private func applyPresentation() {
        guard let presentation else { return }
        for provider in presentation.providers {
            guard let button = statusItems[provider.id]?.button else { continue }
            button.image = StripImageRenderer.render(providerID: provider.id,
                                                     top: provider.stripTop,
                                                     bottom: provider.stripBottom,
                                                     errorBadge: provider.error)
            button.alphaValue = 1.0
        }
    }

    // MARK: - Vigilancia del archivo (rename atómico = evento de escritura en el directorio)

    private func startWatching() {
        let descriptor = open(stateDirectory, O_EVTONLY)
        guard descriptor >= 0 else { return }
        let source = DispatchSource.makeFileSystemObjectSource(
            fileDescriptor: descriptor, eventMask: .write, queue: .main)
        source.setEventHandler { [weak self] in
            MainActor.assumeIsolated { self?.stateFileChanged() }
        }
        source.setCancelHandler {
            close(descriptor)
        }
        source.resume()
        watchSource = source
    }

    /// Coalesce de ráfagas de eventos (el collector escribe tmp + rename).
    private func stateFileChanged() {
        pendingReload?.cancel()
        let work = DispatchWorkItem { [weak self] in
            Task { @MainActor in self?.reload() }
        }
        pendingReload = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25, execute: work)
    }

    @objc private func fallbackTimerFired() {
        reload()
    }

    /// Solo recalcula los textos relativos; no vuelve a leer el archivo.
    @objc private func textTimerFired() {
        render()
    }

    // MARK: - Menú

    private func rebuildMenu() {
        menu.removeAllItems()
        if let presentation {
            if let message = presentation.message {
                menu.addItem(textItem(attributed(from: message, font: NSFont.menuFont(ofSize: 13))))
            } else {
                for (index, provider) in presentation.providers.enumerated() {
                    if index > 0 { menu.addItem(.separator()) }
                    menu.addItem(headerItem(for: provider))
                    for line in provider.lines {
                        menu.addItem(textItem(attributed(from: line, font: NSFont.menuFont(ofSize: 12))))
                    }
                }
            }
        }
        menu.addItem(.separator())
        menu.addItem(actionItem(title: "Abrir panel", action: #selector(openPanel)))
        menu.addItem(actionItem(title: "Actualizar ahora", action: #selector(updateNow)))
        menu.addItem(.separator())
        menu.addItem(actionItem(title: "Salir", action: #selector(quit), keyEquivalent: "q"))
    }

    /// Ítem informativo: sin acción, pero activo para conservar los colores del texto.
    private func textItem(_ attributedTitle: NSAttributedString) -> NSMenuItem {
        let item = NSMenuItem()
        item.attributedTitle = attributedTitle
        item.isEnabled = true
        return item
    }

    private func headerItem(for provider: ProviderPresentation) -> NSMenuItem {
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
        return textItem(header)
    }

    private func actionItem(title: String, action: Selector, keyEquivalent: String = "") -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: keyEquivalent)
        item.target = self
        return item
    }

    private func attributed(from segments: [LineSegment], font: NSFont) -> NSAttributedString {
        let result = NSMutableAttributedString()
        for segment in segments {
            result.append(NSAttributedString(
                string: segment.text,
                attributes: [.font: font, .foregroundColor: color(for: segment.role)]))
        }
        return result
    }

    private func color(for role: ColorRole) -> NSColor {
        switch role {
        case .normal: return .labelColor
        case .secondary: return .secondaryLabelColor
        case .ok: return .systemGreen
        case .warn: return .systemOrange
        case .danger: return .systemRed
        }
    }

    // MARK: - Acciones

    @objc private func openPanel() {
        if let url = URL(string: "http://127.0.0.1:6739/index.html") {
            NSWorkspace.shared.open(url)
        }
    }

    @objc private func updateNow() {
        guard !collectRunning else { return }
        collectRunning = true
        let currentPath = ProcessInfo.processInfo.environment["PATH"] ?? ""
        let collectorPath = (try? String(contentsOfFile: envFilePath, encoding: .utf8))
            .flatMap { EnvFile.path(from: $0, home: home, currentPath: currentPath) }
        CollectRunner.run(home: home, path: collectorPath) { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                self.collectRunning = false
                // 75 (ya había un collect) u otro código: igual refrescamos al terminar.
                self.reload()
            }
        }
    }

    @objc private func quit() {
        NSApplication.shared.terminate(nil)
    }
}
