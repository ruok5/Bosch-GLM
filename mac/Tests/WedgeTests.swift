import XCTest

/// Behaves like the GLM165 as measured on 2026-11-08: `history` is kept newest
/// first here, but listIndex 1 answers the oldest shot. The list holds `depth`
/// shots and drops the oldest when full. Unused slots read as devMode 1 with
/// zeros, past 35 the device returns junk, and ERR shots aren't stored.
/// `onRequest` can change the device mid-scan, like a shot taken during catch-up.
@MainActor
final class FakeDevice: DeviceLink {
    var history: [EDCMeasurement]
    var onRequest: ((FakeDevice, Int) -> Void)?
    weak var wedge: Wedge?
    var requests: [Int] = []
    var beeps = 0
    let depth: Int

    init(_ history: [EDCMeasurement], depth: Int = Wedge.historyDepth,
         onRequest: ((FakeDevice, Int) -> Void)? = nil) {
        self.history = history
        self.depth = depth
        self.onRequest = onRequest
    }

    nonisolated func send(_ data: Data) {
        MainActor.assumeIsolated { handle([UInt8](data)) }
    }

    private func handle(_ b: [UInt8]) {
        if b[1] == Cmd.beeperOn { beeps += 1; return }
        guard b[1] == Cmd.edc, b.count >= 5, (b[3] >> 2) & 0x3F == DevMode.getListItem else { return }
        let idx = Int(b[4] & 0x3F)
        requests.append(idx)
        onRequest?(self, idx)
        let answer: EDCMeasurement
        if idx <= history.count {
            answer = history[history.count - idx]           // #1 is the oldest
        } else if idx <= 35 {
            answer = EDCMeasurement(devMode: DevMode.singleDistance, measId: 0, result: 0)
        } else {
            answer = EDCMeasurement(devMode: DevMode.singleDistance, measId: 0, result: 9.99)  // junk
        }
        wedge?.receivedHistory(answer)
    }

    func shoot(_ shot: EDCMeasurement) {
        if !shot.isError {
            history.insert(shot, at: 0)
            if history.count > depth { history.removeLast() }
        }
        wedge?.receivedLive(shot)
    }
}

@MainActor
final class WedgeTests: XCTestCase {
    private func makeWedge(_ log: ShotLog, depth: Int = Wedge.historyDepth) -> Wedge {
        Wedge(log: log, depth: depth, timing: .instant)
    }

    /// A log the wedge wrote from an empty device: baselined first, then shots.
    private func seed(_ log: ShotLog, _ meters: [Float]) {
        log.markBaselined()
        for (i, x) in meters.enumerated() { log.append(inches(x), kind: "live", measId: 100 + i) }
    }

    private func connect(_ w: Wedge, _ dev: FakeDevice, before: (() -> Void)? = nil) async {
        dev.wedge = w
        w.connected(device: dev, name: "GLM-test")
        before?()
        await w.catchupTask?.value
        await w.settle()
    }

    private func pairs(_ log: ShotLog, from: Int = 0) -> [[String]] {
        rows(log).dropFirst(from).map { [$0[2], $0[3]] }
    }

    func testLiveShotsAndErrPassthrough() async {
        let log = ShotLog(directory: tempDir())
        log.markBaselined()
        let w = makeWedge(log)
        let dev = FakeDevice([])
        await connect(w, dev)
        dev.shoot(m(1.0, id: 1))
        dev.shoot(err(id: 2))
        dev.shoot(err(id: 2))          // double ERR: both logged, never collapsed
        dev.shoot(m(1.0, id: 1))       // repeated notify of shot 1: dropped
        dev.shoot(m(1.0, id: 3))       // same wall again: kept
        await w.settle()
        XCTAssertEqual(pairs(log), [[inches(1), "live"], ["ERR", "live"], ["ERR", "live"],
                                    [inches(1), "live"]])
        XCTAssertEqual(rows(log)[0][4], "1")
        XCTAssertEqual(dev.beeps, 2)   // one per good live shot, none for ERR
    }

    func testCatchupRecoversThenAppendsHeld() async {
        let log = ShotLog(directory: tempDir())
        seed(log, [1, 2])
        let w = makeWedge(log)
        let dev = FakeDevice([m(3), m(3), m(2), m(1)])     // newest first
        await connect(w, dev) { dev.shoot(m(4, id: 77)) }   // shot right after connect
        XCTAssertEqual(pairs(log, from: 2), [[inches(3), "recovered"], [inches(3), "recovered"],
                                             [inches(4), "live"]])
        XCTAssertEqual(rows(log)[2].count, 4)
        XCTAssertEqual(rows(log)[4][4], "77")
        XCTAssertEqual(dev.beeps, 3)   // double for recovered, single for held
    }

    func testShotMidScanTriggersRescan() async {
        let log = ShotLog(directory: tempDir())
        seed(log, [1])
        var shot = false
        let dev = FakeDevice([m(2), m(1)]) { d, idx in
            if idx == 2 && !shot { shot = true; d.shoot(m(5, id: 55)) }   // list shifts
        }
        let w = makeWedge(log)
        await connect(w, dev)
        XCTAssertEqual(pairs(log, from: 1), [[inches(2), "recovered"], [inches(5), "live"]])
        XCTAssertEqual(dev.requests.filter { $0 == 1 }.count, 2)
    }

    func testSkippedModeShotMidScanStillRescans() async {
        let log = ShotLog(directory: tempDir())
        seed(log, [1])
        var shot = false
        let dev = FakeDevice([m(2), m(1)]) { d, idx in
            if idx == 2 && !shot { shot = true; d.shoot(m(7, id: 70, mode: 4)) }   // area
        }
        let w = makeWedge(log)
        await connect(w, dev)
        XCTAssertEqual(pairs(log, from: 1), [[inches(2), "recovered"]])
        XCTAssertEqual(dev.requests.filter { $0 == 1 }.count, 2)
    }

    func testSecondConnectAfterBaselineRecoversOnlyOfflineShots() async throws {
        // The device run on 2026-11-08: a log with a foreign line, a GLM
        // holding 30 old shots, first connect, one live shot, app closed, two
        // shots offline, reconnect. Only the two offline shots may come back.
        let d = tempDir()
        try "34\t2026-11-06T22:04:28\t250\tlive\n"
            .write(to: d.appendingPathComponent("shots.log"), atomically: true, encoding: .utf8)
        let log = ShotLog(directory: d)
        let w = makeWedge(log)
        let dev = FakeDevice((1...30).map { m(Float($0) / 10) }.reversed())
        await connect(w, dev)
        dev.shoot(m(5, id: 50))
        await w.settle()
        w.disconnected()
        dev.wedge = nil
        dev.shoot(m(6, id: 60))
        dev.shoot(m(7, id: 61))
        await connect(w, dev)
        XCTAssertEqual(rows(log).map { [$0[0], $0[2], $0[3]] },
                       [["34", "250", "live"], ["35", inches(5), "live"],
                        ["36", inches(6), "recovered"], ["37", inches(7), "recovered"]])
        // A fresh ShotLog (app relaunch) reads the saved baseline the same way.
        await connect(makeWedge(ShotLog(directory: d)), dev)
        XCTAssertEqual(rows(log).count, 4)
    }

    func testFullHistoryOldestFirstRecoversNewestShots() async {
        // Today's device session: the log has shots 1-30, then 4 were taken
        // offline, so the GLM's list is now 5...30 then 31-34.
        let log = ShotLog(directory: tempDir())
        seed(log, (1...30).map { Float($0) / 10 })
        let onDevice = (5...34).map { m(Float($0) / 10) }
        let dev = FakeDevice(onDevice.reversed())
        await connect(makeWedge(log), dev)
        XCTAssertEqual(pairs(log, from: 30), (31...34).map { [inches(Float($0) / 10), "recovered"] })
        XCTAssertEqual(dev.requests.max(), Wedge.historyDepth)   // never reads the junk past 30
    }

    func testStoredIndirectShotDoesNotEndScan() async {
        // devMode 17 entries can carry a negative result; they're skipped,
        // not taken for the end of the list.
        let log = ShotLog(directory: tempDir())
        seed(log, [1])
        let indirect = EDCMeasurement(devMode: 17, measId: 1, result: -2.9359, comp1: 3.0754,
                                      comp2: 6.0113)
        await connect(makeWedge(log), FakeDevice([m(2), indirect, m(1)]))
        XCTAssertEqual(pairs(log, from: 1), [[inches(2), "recovered"]])
    }

    func testErrDuringCatchupDoesNotDisturbAlignment() async {
        let log = ShotLog(directory: tempDir())
        seed(log, [1])
        var shot = false
        let dev = FakeDevice([m(2), m(1)]) { d, idx in
            if idx == 2 && !shot { shot = true; d.shoot(err(id: 9)) }
        }
        await connect(makeWedge(log), dev)
        XCTAssertEqual(pairs(log, from: 1), [[inches(2), "recovered"], ["ERR", "live"]])
    }

    func testFirstConnectBaselines() async throws {
        let d = tempDir()
        try "34\t2026-11-06T22:04:28\t250\tlive\n"
            .write(to: d.appendingPathComponent("shots.log"), atomically: true, encoding: .utf8)
        let log = ShotLog(directory: d)
        await connect(makeWedge(log), FakeDevice([m(2), m(1)]))
        XCTAssertEqual(rows(log).count, 1)                  // nothing backfilled
        XCTAssertTrue(ShotLog(directory: d).baselined)
    }

    func testGapLineAheadOfRecovered() async {
        let log = ShotLog(directory: tempDir())
        seed(log, [1])
        await connect(makeWedge(log, depth: 2), FakeDevice([m(9), m(8)]))
        XCTAssertEqual(rows(log).dropFirst().map { [$0[0], $0[2], $0[3]] },
                       [["2", "GAP", "recovered"], ["3", inches(8), "recovered"],
                        ["4", inches(9), "recovered"]])
    }

    func testDisconnectMidCatchupLeavesHeldOnDevice() async {
        let log = ShotLog(directory: tempDir())
        seed(log, [1])
        let w = Wedge(log: log, timing: WedgeTiming(catchupDelay: .milliseconds(200),
                                                    responseTimeout: .milliseconds(50),
                                                    retryDelay: .zero, beepDelay: .zero,
                                                    beepLength: .zero, beepGap: .zero))
        let dev = FakeDevice([m(1)])
        dev.wedge = w
        w.connected(device: dev, name: "GLM-test")
        dev.shoot(m(6, id: 6))
        w.disconnected()
        XCTAssertEqual(rows(log).count, 1)                  // held shot not logged yet
        await connect(w, dev)                               // comes back as recovered
        XCTAssertEqual(pairs(log, from: 1), [[inches(6), "recovered"]])
    }

    func testFrameDispatch() {
        let log = ShotLog(directory: tempDir())
        log.markBaselined()
        let w = makeWedge(log)
        // A live EDC frame for 1.0 m, single distance, measId 9.
        var payload = Data([DevMode.singleDistance << 2, 0x08, 9, 0])
        withUnsafeBytes(of: Float(1.0).bitPattern.littleEndian) { payload.append(contentsOf: $0) }
        payload.append(Data(count: 8))
        let frames = FrameDecoder().feed(encode(Frame.request(cmd: Cmd.edc, payload: payload)))
        XCTAssertEqual(frames.count, 1)
        frames.forEach(w.handle(frame:))
        XCTAssertEqual(rows(log).map { [$0[2], $0[3], $0[4]] }, [[inches(1), "live", "9"]])
    }
}
