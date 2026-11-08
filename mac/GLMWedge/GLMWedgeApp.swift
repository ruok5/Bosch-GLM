import SwiftUI

@main
struct GLMWedgeApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model = AppModel()

    var body: some Scene {
        Window("GLM Wedge", id: "main") {
            ContentView()
                .environmentObject(model.wedge)
                .environmentObject(model.doorbell)
        }
        .defaultSize(width: 600, height: 660)

        Settings {
            SettingsView()
                .environmentObject(model.wedge)
                .environmentObject(model.doorbell)
        }
    }
}

/// Closing the window quits the app, which stops logging.
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }
}

@MainActor
final class AppModel: ObservableObject {
    let wedge: Wedge
    let doorbell: Doorbell
    private var bluetooth: GLMBluetooth?

    init() {
        // `-logDir /path` on the command line points the app at a scratch log.
        let dir = UserDefaults.standard.string(forKey: "logDir")
            .map { URL(fileURLWithPath: $0, isDirectory: true) } ?? ShotLog.defaultDirectory
        wedge = Wedge(log: ShotLog(directory: dir))
        doorbell = Doorbell()
        wedge.doorbell = doorbell
        bluetooth = GLMBluetooth(wedge: wedge)
    }
}
