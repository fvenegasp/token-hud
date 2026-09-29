import AppKit

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var controller: StatusBarController?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApplication.shared.setActivationPolicy(.accessory)
        let controller = StatusBarController()
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--render-strip"), i + 1 < args.count {
            controller.renderStrips(to: args[i + 1])
            exit(0)
        }
        self.controller = controller
        controller.start()
    }
}
