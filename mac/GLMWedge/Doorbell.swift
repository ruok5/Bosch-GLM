import AppKit
import ApplicationServices

/// Nudges the mouse so AutoCAD's grread loop wakes up and reads the log.
///
/// After a line is appended, if AutoCAD is frontmost, wait until the mouse
/// has been still for `stillSeconds`, then post a one-pixel move right and
/// back. If the mouse moves in the meantime, that motion already woke grread,
/// so do nothing. Never presses a key. Posting events needs Accessibility.
@MainActor
final class Doorbell: ObservableObject, DoorbellRinging {
    @Published var enabled: Bool {
        didSet { UserDefaults.standard.set(enabled, forKey: "doorbell") }
    }
    @Published private(set) var trusted = AXIsProcessTrusted()
    @Published private(set) var lastRing: Date?
    var stillSeconds = 2.0
    private var pending: Task<Void, Never>?

    init() {
        enabled = UserDefaults.standard.object(forKey: "doorbell") as? Bool ?? true
    }

    func refreshTrust() {
        trusted = AXIsProcessTrusted()
    }

    /// Shows the system prompt that opens Accessibility settings.
    func requestTrust() {
        let key = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        trusted = AXIsProcessTrustedWithOptions([key: true] as CFDictionary)
    }

    static func autocadFrontmost() -> Bool {
        let id = NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? ""
        return id.lowercased().hasPrefix("com.autodesk.autocad")
    }

    static func secondsSinceMouseMoved() -> Double {
        CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: .mouseMoved)
    }

    static func nudge() {
        guard let here = CGEvent(source: nil)?.location else { return }
        for x in [here.x + 1, here.x] {
            CGEvent(mouseEventSource: nil, mouseType: .mouseMoved,
                    mouseCursorPosition: CGPoint(x: x, y: here.y),
                    mouseButton: .left)?.post(tap: .cghidEventTap)
        }
    }

    /// Call after appending a line. One pending ring covers any number of
    /// lines: LASER reads everything new when it wakes.
    func ring() {
        guard enabled, pending == nil else { return }
        pending = Task { [weak self] in
            await self?.waitAndNudge()
            self?.pending = nil
        }
    }

    private func waitAndNudge() async {
        let t0 = Date()
        while Self.autocadFrontmost() {
            let idle = Self.secondsSinceMouseMoved()
            if idle < Date().timeIntervalSince(t0) { return }   // moved since the append
            if idle >= stillSeconds {
                Self.nudge()
                lastRing = Date()
                return
            }
            try? await Task.sleep(for: .seconds(stillSeconds - idle + 0.05))
        }
    }
}
