import AppKit
import Darwin
import CuotaCore

/// Un NSStatusItem por proveedor, un menú compartido que se reconstruye en cada refresco.
/// Coordinador de dos modos excluyentes: "Barra de menú" (ítems) y "Barra lateral"
/// (panel flotante, sin ítems). Datos: ~/.cache/cuota/state.json (vigilado por
/// rename atómico + timer de respaldo).
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

    private let settings = DisplayModeSettings.shared
    private var sidebar: SidebarPanel?
    private var hotKey: HotKeyRegistrar?

    private var home: String { NSHomeDirectory() }
    private var statePath: String { home + "/.cache/cuota/state.json" }
    private var stateDirectory: String { home + "/.cache/cuota" }
    private var envFilePath: String { home + "/.config/cuota/env" }

    func start() {
        menu.autoenablesItems = false
        installStatusItems()
        reload()
        startWatching()
        // Respaldo por si el watch pierde un evento; y textos relativos ("hace N min") frescos.
        fallbackTimer = Timer.scheduledTimer(timeInterval: 60, target: self,
                                             selector: #selector(fallbackTimerFired), userInfo: nil, repeats: true)
        textTimer = Timer.scheduledTimer(timeInterval: 30, target: self,
                                         selector: #selector(textTimerFired), userInfo: nil, repeats: true)
        // Modo persistido (por defecto "Barra de menú") + atajo global ⌥⌘L.
        settings.onChange = { [weak self] in self?.applyMode() }
        applyMode()
        hotKey = HotKeyRegistrar.toggleDisplayMode { [weak self] in
            guard let self else { return }
            self.settings.mode = self.settings.mode == .menuBar ? .sidebar : .menuBar
        }
        hotKey?.register()
    }

    /// NSStatusBar coloca cada ítem nuevo a la IZQUIERDA del anterior: crear en orden inverso.
    private func installStatusItems() {
        guard statusItems.isEmpty else { return }
        for entry in Presenter.order.reversed() {
            let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
            item.menu = menu
            statusItems[entry.id] = item
        }
    }

    private func removeStatusItems() {
        for item in statusItems.values { NSStatusBar.system.removeStatusItem(item) }
        statusItems.removeAll()
    }

    /// Modos excluyentes: al activar la barra lateral no hay ítems de barra de menú;
    /// al volver, se recrean en el mismo orden.
    private func applyMode() {
        if settings.mode == .sidebar {
            removeStatusItems()
            if sidebar == nil {
                let panel = SidebarPanel()
                (panel.contentView as? SidebarContentView)?.delegate = self
                if let presentation { panel.update(presentation) }
                panel.applyPosition()
                panel.orderFrontRegardless()
                sidebar = panel
            } else {
                sidebar?.applySize()
            }
        } else {
            sidebar?.orderOut(nil)
            sidebar = nil
            installStatusItems()
            applyPresentation()
        }
        rebuildMenu()
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
        hotKey?.unregister()
        hotKey = nil
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
        if let presentation { sidebar?.update(presentation) }
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
        menu.addItem(sectionHeader("Mostrar en", hint: "⌥⌘L alterna"))
        menu.addItem(modeItem("Barra de menú", mode: .menuBar))
        menu.addItem(modeItem("Barra lateral", mode: .sidebar))
        menu.addItem(sectionHeader("Borde de la barra lateral"))
        menu.addItem(edgeItem("Derecho", edge: .right))
        menu.addItem(edgeItem("Izquierdo", edge: .left))
        menu.addItem(sectionHeader("Tamaño de la barra lateral"))
        menu.addItem(sizeItem("Compacto", size: .compact))
        menu.addItem(sizeItem("Normal", size: .normal))
        menu.addItem(sizeItem("Grande", size: .large))
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
        textItem(PresentationText.header(for: provider))
    }

    /// Cabecera de sección informativa (p. ej. "Mostrar en"), con pista a la derecha.
    private func sectionHeader(_ title: String, hint: String? = nil) -> NSMenuItem {
        let text = NSMutableAttributedString(
            string: title,
            attributes: [.font: NSFont.systemFont(ofSize: 11),
                         .foregroundColor: NSColor.secondaryLabelColor])
        if let hint {
            text.append(NSAttributedString(
                string: "  ·  \(hint)",
                attributes: [.font: NSFont.menuBarFont(ofSize: 9),
                             .foregroundColor: NSColor.tertiaryLabelColor]))
        }
        let item = textItem(text)
        item.isEnabled = false
        return item
    }

    /// Ítem exclusivo de modo de visualización con marca en el vigente.
    private func modeItem(_ title: String, mode: DisplayMode) -> NSMenuItem {
        let item = actionItem(title: title, action: #selector(selectDisplayMode))
        item.representedObject = mode.rawValue
        item.state = settings.mode == mode ? .on : .off
        return item
    }

    /// Borde de la barra lateral: solo habilitado en ese modo.
    private func edgeItem(_ title: String, edge: SidebarEdge) -> NSMenuItem {
        let item = actionItem(title: title, action: #selector(selectSidebarEdge))
        item.representedObject = edge.rawValue
        item.state = settings.edge == edge ? .on : .off
        item.isEnabled = settings.mode == .sidebar
        return item
    }

    /// Tamaño de la barra lateral: solo habilitado en ese modo.
    private func sizeItem(_ title: String, size: SidebarSize) -> NSMenuItem {
        let item = actionItem(title: title, action: #selector(selectSidebarSize))
        item.representedObject = size.rawValue
        item.state = settings.sidebarSize == size ? .on : .off
        item.isEnabled = settings.mode == .sidebar
        return item
    }

    private func actionItem(title: String, action: Selector, keyEquivalent: String = "") -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: keyEquivalent)
        item.target = self
        return item
    }

    private func attributed(from segments: [LineSegment], font: NSFont) -> NSAttributedString {
        PresentationText.line(segments, font: font)
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

    // MARK: - Modo de visualización (menú y barra lateral)

    @objc private func selectDisplayMode(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String,
              let mode = DisplayMode(rawValue: raw) else { return }
        settings.mode = mode
    }

    @objc private func selectSidebarEdge(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String,
              let edge = SidebarEdge(rawValue: raw) else { return }
        settings.edge = edge
    }

    @objc private func selectSidebarSize(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String,
              let size = SidebarSize(rawValue: raw) else { return }
        settings.sidebarSize = size
    }
}

// MARK: - SidebarDelegate (menú, panel web y datos para el globo)

extension StatusBarController: SidebarDelegate {
    func openMenu(in view: NSView, at point: NSPoint) {
        // El mismo menú de Token HUD, posicionado junto a la barra lateral.
        rebuildMenu()
        menu.popUp(positioning: nil, at: point, in: view)
    }

    func openWebPanel() {
        openPanel()
    }

    func presentation(for providerID: String) -> ProviderPresentation? {
        presentation?.providers.first { $0.id == providerID }
    }
}
