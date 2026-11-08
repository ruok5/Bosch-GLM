import Foundation
import Combine

// The wedge: turns the GLM's stream into ~/.glm/shots.log for LASER.lsp.
// Never types into AutoCAD, never interprets gestures.
//
// On every (re)connect it reads the device history and appends the shots the
// log doesn't have yet as `recovered`, oldest first. Live shots that arrive
// while that runs are held and appended after the recovered lines, so the log
// always runs in the order the shots were taken. That order is also what lets
// the next catch-up line the history up against the log tail.

/// Writes raw frames to the GLM. CoreBluetooth in the app, a fake in tests.
protocol DeviceLink: AnyObject {
    func send(_ data: Data)
}

@MainActor
protocol DoorbellRinging: AnyObject {
    func ring()
}

struct WedgeTiming {
    var catchupDelay: Duration = .milliseconds(2500)   // let autosync settle
    var responseTimeout: Duration = .milliseconds(1500)
    var retryDelay: Duration = .seconds(3)
    var beepDelay: Duration = .milliseconds(150)       // after the GLM's own beep
    var beepLength: Duration = .milliseconds(30)
    var beepGap: Duration = .milliseconds(80)

    static let instant = WedgeTiming(catchupDelay: .zero, responseTimeout: .milliseconds(50),
                                     retryDelay: .zero, beepDelay: .zero,
                                     beepLength: .zero, beepGap: .zero)
}

enum LinkState: Equatable {
    case bluetoothOff(String)
    case searching
    case connecting(String)
    case connected(String)
}

struct WedgeEvent: Identifiable {
    let id = UUID()
    let time = Date()
    let text: String
    let warning: Bool
}

@MainActor
final class Wedge: ObservableObject {
    // Measured on the GLM165-27C6, 2026-11-08: history is listIndex 1...30,
    // oldest first (#1 drops off when a shot lands on a full list). Slots
    // past the last shot read as devMode 1 / result 0, and 36+ return junk,
    // so never read past `historyDepth`. ERR shots aren't stored. A history
    // response carries a fresh meas_id from the device's message counter.
    static let historyDepth = 30
    static let historyKeepsErrors = false
    static let catchupAttempts = 5

    @Published private(set) var link: LinkState = .searching
    @Published private(set) var catchingUp = false
    @Published private(set) var recent: [LogLine] = []      // newest first
    @Published private(set) var nextSeq = 1
    @Published private(set) var events: [WedgeEvent] = []

    let log: ShotLog
    weak var doorbell: DoorbellRinging?
    var notifyURL: () -> URL? = {
        UserDefaults.standard.string(forKey: "notifyURL")
            .flatMap { $0.isEmpty ? nil : URL(string: $0) }
    }
    let keepsErrors: Bool
    let depth: Int
    let timing: WedgeTiming

    private var device: DeviceLink?
    private var scanDirty = false
    private var held: [(value: String, measId: Int, when: Date)] = []
    private var lastLive: (measId: Int, value: String)?
    private(set) var catchupTask: Task<Void, Never>?
    private var histBuffer: [EDCMeasurement] = []
    private var histWaiter: (token: Int, cont: CheckedContinuation<EDCMeasurement?, Never>)?
    private var waiterToken = 0
    private var beepChain: Task<Void, Never>?
    private var logStamp: (Date, Int)?

    init(log: ShotLog, keepsErrors: Bool = Wedge.historyKeepsErrors,
         depth: Int = Wedge.historyDepth, timing: WedgeTiming = WedgeTiming()) {
        self.log = log
        self.keepsErrors = keepsErrors
        self.depth = depth
        self.timing = timing
        reloadRecent()
    }

    // MARK: - link state (called by the Bluetooth layer)

    func bluetoothUnavailable(_ reason: String) {
        link = .bluetoothOff(reason)
    }

    func searching() {
        if case .connected = link { return }
        link = .searching
    }

    func connecting(_ name: String) {
        link = .connecting(name)
    }

    func connected(device: DeviceLink, name: String) {
        self.device = device
        link = .connected(name)
        event("Connected to \(name)")
        // Hold from the moment we connect: a shot logged live before the
        // history read would sit ahead of the offline shots in the log.
        catchingUp = true
        held = []
        lastLive = nil
        histBuffer = []
        catchupTask?.cancel()
        catchupTask = Task { [weak self] in await self?.runCatchup(device) }
    }

    func disconnected() {
        catchupTask?.cancel()
        catchupTask = nil
        resumeWaiter(nil)
        let n = held.count
        device = nil
        catchingUp = false
        held = []
        link = .searching
        let extra = n > 0 ? " (\(n) unlogged shot\(n == 1 ? "" : "s") stay on the GLM for the next catch-up)" : ""
        event("Lost the GLM\(extra)", warning: true)
        notify("GLM lost")
    }

    // MARK: - frames

    func handle(frame: Frame) {
        guard frame.payload.count >= 16,
              let m = EDCMeasurement.fromPayload(frame.payload) else { return }
        switch frame.type {
        case .request where frame.cmd == Cmd.edc: receivedLive(m)
        case .response: receivedHistory(m)
        default: break
        }
    }

    func receivedLive(_ m: EDCMeasurement) {
        if catchingUp && m.devMode != DevMode.noAction {
            // Anything the device stores (even a mode we skip) shifts its
            // history list under the scan.
            scanDirty = true
        }
        guard let value = classify(m) else { return }
        if value != Shot.err {
            // Guard against a repeated notification of the same shot. ERRs are
            // never deduped: two in a row is LASER's reset gesture.
            if let last = lastLive, last.measId == Int(m.measId), last.value == value { return }
            lastLive = (Int(m.measId), value)
        }
        if catchingUp {
            held.append((value, Int(m.measId), Date()))
            return
        }
        log.append(value, kind: Shot.live, measId: Int(m.measId))
        reloadRecent()
        afterAppend(beeps: value == Shot.err ? 0 : 1)
    }

    func receivedHistory(_ m: EDCMeasurement) {
        if histWaiter != nil {
            resumeWaiter(m)
        } else {
            histBuffer.append(m)
        }
    }

    // MARK: - catch-up

    private func resumeWaiter(_ m: EDCMeasurement?) {
        guard let w = histWaiter else { return }
        histWaiter = nil
        w.cont.resume(returning: m)
    }

    private func nextHistory() async -> EDCMeasurement? {
        if !histBuffer.isEmpty { return histBuffer.removeFirst() }
        waiterToken += 1
        let token = waiterToken
        let timeout = timing.responseTimeout
        return await withCheckedContinuation { cont in
            histWaiter = (token, cont)
            Task { @MainActor [weak self] in
                try? await Task.sleep(for: timeout)
                guard let self, let w = self.histWaiter, w.token == token else { return }
                self.resumeWaiter(nil)
            }
        }
    }

    private func request(_ device: DeviceLink, _ idx: Int) async -> EDCMeasurement? {
        histBuffer.removeAll()
        device.send(encode(edcRequestHistoryItem(idx)))
        return await nextHistory()
    }

    /// Read the device history (stored oldest first) and return it newest
    /// first, with the number of slots in use, or nil if the first read got
    /// no answer.
    private func scan(_ device: DeviceLink) async -> (values: [String], count: Int)? {
        var values: [String] = []
        var count = 0
        for idx in 1...depth {
            var m = await request(device, idx)
            if Task.isCancelled { return nil }
            if m == nil {
                m = await request(device, idx)              // one retry
                if Task.isCancelled { return nil }
            }
            guard let m else {
                if idx == 1 { return nil }
                break                                       // end of list
            }
            if m.isError {
                if !keepsErrors { break }                   // end-of-list marker
                values.append(Shot.err)
            } else if m.isEmptySlot {
                break                                       // past the newest shot
            } else if let v = classify(m) {
                values.append(v)
            }                                               // else area, indirect...: skip
            count += 1
        }
        return (values.reversed(), count)
    }

    private func runCatchup(_ device: DeviceLink) async {
        try? await Task.sleep(for: timing.catchupDelay)
        if Task.isCancelled { return }
        var result: (values: [String], count: Int)?
        for _ in 0..<Self.catchupAttempts {
            scanDirty = false
            let scanned = await scan(device)
            if Task.isCancelled { return }
            if let scanned, !scanDirty { result = scanned; break }
            if scanned == nil {
                try? await Task.sleep(for: timing.retryDelay)
                if Task.isCancelled { return }
            }
        }
        finishCatchup(result)
    }

    /// Append recovered lines then held live lines. Synchronous, so no live
    /// frame can slip in between.
    private func finishCatchup(_ scanned: (values: [String], count: Int)?) {
        let held = self.held
        self.held = []
        catchingUp = false
        catchupTask = nil
        var recovered = 0
        var gap = false
        if let scanned {
            let tail = log.tailValues(scanned.values.count, includeErrors: keepsErrors)
            let plan = planCatchup(historyNewestFirst: scanned.values, tail: tail,
                                   heldLive: held.map(\.value), keepsErrors: keepsErrors,
                                   full: scanned.count >= depth, baselined: log.baselined)
            if plan.baseline {
                log.markBaselined(history: plan.baselineHistory)   // before held lines
                event("Baseline: \(scanned.count) shots on the GLM, none backfilled")
            } else if plan.heldMismatch {
                event("Catch-up skipped: held shots weren't at the end of the GLM's history",
                      warning: true)
            } else {
                let when = Date()
                if plan.gap {
                    gap = true
                    log.append(Shot.gap, kind: Shot.recovered, when: when)
                    event("Shots were lost off the GLM's history. GAP logged.", warning: true)
                    notify("GLM: shots lost off the device history (GAP logged)")
                }
                for v in plan.recovered {
                    log.append(v, kind: Shot.recovered, when: when)
                }
                recovered = plan.recovered.count
            }
            event("Recovered \(recovered)")
            notify("GLM reconnected, \(recovered) recovered")
        } else {
            event("Catch-up failed: couldn't read the GLM's history. Offline shots not recovered.",
                  warning: true)
            notify("GLM catch-up failed")
        }
        var liveBeep = 0
        for h in held {
            log.append(h.value, kind: Shot.live, measId: h.measId, when: h.when)
            if h.value != Shot.err { liveBeep = 1 }
        }
        reloadRecent()
        if recovered > 0 || !held.isEmpty || gap {
            afterAppend(beeps: recovered > 0 ? 2 : 0, then: liveBeep)
        }
    }

    // MARK: - side effects

    private func afterAppend(beeps: Int, then: Int = 0) {
        doorbell?.ring()
        guard beeps + then > 0, let device else { return }
        let prev = beepChain
        let t = timing
        beepChain = Task { @MainActor in
            await prev?.value
            try? await Task.sleep(for: t.beepDelay)
            for i in 0..<beeps {
                if i > 0 { try? await Task.sleep(for: t.beepGap) }
                await Self.beep(device, t)
            }
            if then > 0 {
                if beeps > 0 { try? await Task.sleep(for: .milliseconds(400)) }
                await Self.beep(device, t)
            }
        }
    }

    private static func beep(_ device: DeviceLink, _ t: WedgeTiming) async {
        device.send(encode(beeperFrame(on: true)))
        try? await Task.sleep(for: t.beepLength)
        device.send(encode(beeperFrame(on: false)))
    }

    /// Wait for queued beeps (tests).
    func settle() async {
        await beepChain?.value
    }

    private func notify(_ text: String) {
        guard let url = notifyURL() else { return }
        var req = URLRequest(url: url, timeoutInterval: 5)
        req.httpMethod = "POST"
        req.setValue("text/plain", forHTTPHeaderField: "Content-Type")
        req.httpBody = Data(text.utf8)
        URLSession.shared.dataTask(with: req).resume()
    }

    private func event(_ text: String, warning: Bool = false) {
        events.insert(WedgeEvent(text: text, warning: warning), at: 0)
        if events.count > 50 { events.removeLast(events.count - 50) }
    }

    // MARK: - log view

    func reloadRecent() {
        recent = Array(log.lines().suffix(500).reversed())
        nextSeq = log.seq + 1
        logStamp = Self.stamp(log.url)
    }

    /// Pick up outside changes (e.g. the log cleared by hand).
    func reloadIfChanged() {
        let changed: Bool
        switch (Self.stamp(log.url), logStamp) {
        case (nil, nil): changed = false
        case let (now?, then?): changed = now != then
        default: changed = true
        }
        if changed { reloadRecent() }
    }

    private static func stamp(_ url: URL) -> (Date, Int)? {
        guard let a = try? FileManager.default.attributesOfItem(atPath: url.path) else { return nil }
        return (a[.modificationDate] as? Date ?? .distantPast, (a[.size] as? Int) ?? 0)
    }
}
