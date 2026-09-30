import Carbon.HIToolbox
import Foundation

/// Constante de Carbon no expuesta por el submodule en Swift.
private let hotKeyPressedEventKind: UInt32 = 5 // kEventHotKeyPressedEvent

/// Atajo global con Carbon RegisterEventHotKey (no requiere permiso de
/// Accesibilidad). Solo un atajo registrado a la vez.
@MainActor
final class HotKeyRegistrar {
    private var hotKeyRef: EventHotKeyRef?
    private var handlerRef: EventHandlerRef?
    /// Contexto puente: el callback de Carbon es una función C sin closure con estado.
    private var context: Box?
    private let keyCode: UInt32
    private let modifiers: UInt32
    private let action: () -> Void

    private final class Box {
        var handler: () -> Void
        init(_ handler: @escaping () -> Void) { self.handler = handler }
    }

    init(keyCode: UInt32, modifiers: UInt32, action: @escaping () -> Void) {
        self.keyCode = keyCode
        self.modifiers = modifiers
        self.action = action
    }

    /// Conveniencia para ⌥⌘L (alternar modo de visualización).
    static func toggleDisplayMode(_ action: @escaping () -> Void) -> HotKeyRegistrar {
        HotKeyRegistrar(keyCode: UInt32(kVK_ANSI_L), modifiers: UInt32(cmdKey | optionKey), action: action)
    }

    func register() {
        guard hotKeyRef == nil else { return }
        let box = Box { [action] in action() }
        // El propietario del puente es `context` (strong); a Carbon se pasa sin retain.
        let userData = Unmanaged.passUnretained(box).toOpaque()

        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard),
                                 eventKind: hotKeyPressedEventKind)
        // Un único handler de app: se compara la firma del atajo en el callback.
        let callback: EventHandlerUPP = { _, event, userData in
            guard let event, let userData else { return noErr }
            var hotKeyID = EventHotKeyID()
            GetEventParameter(event, EventParamName(kEventParamDirectObject),
                              EventParamType(typeEventHotKeyID), nil,
                              MemoryLayout<EventHotKeyID>.size, nil, &hotKeyID)
            if hotKeyID.signature == "THUD".fourCharCode {
                let box = Unmanaged<HotKeyRegistrar.Box>.fromOpaque(userData).takeUnretainedValue()
                // Carbon entrega en el hilo principal del run loop de la app.
                MainActor.assumeIsolated { box.handler() }
            }
            return noErr
        }
        InstallEventHandler(GetApplicationEventTarget(), callback, 1, &spec, userData, &handlerRef)

        var ref: EventHotKeyRef?
        RegisterEventHotKey(keyCode, modifiers, EventHotKeyID(signature: "THUD".fourCharCode, id: 1),
                            GetApplicationEventTarget(), 0, &ref)
        guard let ref else { return }
        hotKeyRef = ref
        context = box
    }

    func unregister() {
        if let hotKeyRef { UnregisterEventHotKey(hotKeyRef) }
        if let handlerRef { RemoveEventHandler(handlerRef) }
        hotKeyRef = nil
        handlerRef = nil
        context = nil
    }
}

extension String {
    /// Firma de 4 caracteres para EventHotKeyID.
    var fourCharCode: OSType {
        prefix(4).reduce(OSType(0)) { ($0 << 8) + OSType($1.utf8.first ?? 0) }
    }
}
