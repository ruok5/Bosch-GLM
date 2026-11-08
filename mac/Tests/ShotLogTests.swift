import XCTest

func m(_ meters: Float, id: UInt16 = 1, mode: UInt8 = DevMode.singleDistance) -> EDCMeasurement {
    EDCMeasurement(devMode: mode, measId: id, result: meters)
}

func err(id: UInt16 = 1) -> EDCMeasurement {
    EDCMeasurement(devMode: DevMode.errorResponse, measId: id, result: 1)
}

func inches(_ meters: Float) -> String {
    String(format: "%.4f", Double(meters) * Shot.inchesPerMeter)
}

func tempDir() -> URL {
    let d = FileManager.default.temporaryDirectory
        .appendingPathComponent("glmwedge-\(UUID().uuidString)", isDirectory: true)
    try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
    return d
}

func rows(_ log: ShotLog) -> [[String]] {
    let text = (try? String(contentsOf: log.url, encoding: .utf8)) ?? ""
    return text.split(separator: "\n").map {
        $0.split(separator: "\t", omittingEmptySubsequences: false).map(String.init)
    }
}

final class ShotLogTests: XCTestCase {
    // MARK: line format

    func testFormatLine() {
        var c = DateComponents()
        c.year = 2026; c.month = 11; c.day = 7; c.hour = 8; c.minute = 5; c.second = 9
        let t = Calendar.current.date(from: c)!
        XCTAssertEqual(formatLine(seq: 12, when: t, value: "120.5000", kind: "live", measId: 4321),
                       "12\t2026-11-07T08:05:09\t120.5000\tlive\t4321\n")
        XCTAssertEqual(formatLine(seq: 13, when: t, value: "ERR", kind: "recovered"),
                       "13\t2026-11-07T08:05:09\tERR\trecovered\n")
    }

    func testClassify() {
        XCTAssertEqual(classify(m(1.0)), "39.3701")        // 1 m, 4 dp
        XCTAssertEqual(classify(err()), "ERR")
        XCTAssertNil(classify(m(1.0, mode: 0)))            // heartbeat
        XCTAssertNil(classify(m(1.0, mode: 2)))            // continuous: not yet
        XCTAssertNil(classify(m(1.0, mode: 4)))            // area
        XCTAssertNil(classify(m(0)))
    }

    func testParseLineToleratesForeignLines() {
        XCTAssertEqual(parseLine("7\t2026-11-06T22:04:23\t250\tlive")?.seq, 7)
        XCTAssertNil(parseLine("# a comment"))
        XCTAssertNil(parseLine(""))
    }

    func testHistoryRequestBytesMatchPython() {
        // glm.protocol.messages.edc_request_history_item(1) == c05502e901dc
        XCTAssertEqual(encode(edcRequestHistoryItem(1)).map { String(format: "%02x", $0) }.joined(),
                       "c05502e901dc")
        XCTAssertEqual(encode(beeperFrame(on: true)).map { String(format: "%02x", $0) }.joined(),
                       "c04500d0")
    }

    // MARK: seq persistence

    func testSeqPersistsAcrossRestarts() {
        let d = tempDir()
        let log = ShotLog(directory: d)
        XCTAssertEqual(log.append("10.0000", kind: "live", measId: 1), 1)
        XCTAssertEqual(log.append("ERR", kind: "live", measId: 2), 2)
        let again = ShotLog(directory: d)
        XCTAssertEqual(again.seq, 2)
        XCTAssertEqual(again.append("11.0000", kind: "live", measId: 3), 3)
        XCTAssertEqual(rows(again).map { $0[0] }, ["1", "2", "3"])
    }

    func testSeqSurvivesLogMovedAside() throws {
        let d = tempDir()
        let log = ShotLog(directory: d)
        for i in 0..<5 { log.append("1.0000", kind: "live", measId: i) }
        try FileManager.default.moveItem(at: log.url, to: d.appendingPathComponent("old.log"))
        XCTAssertEqual(ShotLog(directory: d).append("2.0000", kind: "live"), 6)
    }

    func testSeqContinuesAfterForeignLines() throws {
        let d = tempDir()
        try "33\t2026-11-06T22:04:23\t250\tlive\n34\t2026-11-06T22:04:28\t250\tlive\n"
            .write(to: d.appendingPathComponent("shots.log"), atomically: true, encoding: .utf8)
        XCTAssertEqual(ShotLog(directory: d).append("1.0000", kind: "live"), 35)
    }

    func testTailSkipsGapAndOptionallyErr() {
        let log = ShotLog(directory: tempDir())
        for v in ["1.0000", "ERR", "GAP", "2.0000"] { log.append(v, kind: "live") }
        XCTAssertEqual(log.tailValues(10, includeErrors: false), ["1.0000", "2.0000"])
        XCTAssertEqual(log.tailValues(10, includeErrors: true), ["1.0000", "ERR", "2.0000"])
        XCTAssertEqual(log.tailValues(1, includeErrors: true), ["2.0000"])
    }

    func testTailStartsFromSavedBaseline() throws {
        let d = tempDir()
        try "34\t2026-11-06T22:04:28\t250\tlive\n"
            .write(to: d.appendingPathComponent("shots.log"), atomically: true, encoding: .utf8)
        let log = ShotLog(directory: d)
        log.markBaselined(history: ["a", "b"])
        XCTAssertEqual(log.tailValues(10, includeErrors: false), ["a", "b"])   // 250 left out
        log.append("c", kind: "live")
        log.append("GAP", kind: "recovered")
        log.append("ERR", kind: "live")
        XCTAssertEqual(ShotLog(directory: d).tailValues(10, includeErrors: false), ["a", "b", "c"])
        XCTAssertEqual(log.tailValues(2, includeErrors: true), ["c", "ERR"])
        try FileManager.default.removeItem(at: log.url)                      // moved aside
        XCTAssertEqual(log.tailValues(10, includeErrors: false), [])
    }

    // MARK: alignment

    private func plan(_ histOldestFirst: [String], _ tail: [String], held: [String] = [],
                      full: Bool = false, baselined: Bool = true,
                      keepsErrors: Bool = false) -> CatchupPlan {
        planCatchup(historyNewestFirst: histOldestFirst.reversed(), tail: tail, heldLive: held,
                    keepsErrors: keepsErrors, full: full, baselined: baselined)
    }

    func testOverlap() {
        XCTAssertEqual(overlap(["a", "b", "c"], ["x", "a", "b", "c"]), 3)
        XCTAssertEqual(overlap(["a", "b", "c", "d"], ["x", "a", "b"]), 2)
        XCTAssertEqual(overlap(["c", "d"], ["a", "b"]), 0)
    }

    func testRecoversOfflineShotsOnly() {
        let p = plan(["a", "b", "c", "d"], ["x", "a", "b"])
        XCTAssertEqual(p.recovered, ["c", "d"]); XCTAssertFalse(p.gap)
    }

    func testDeliberateRepeatIsRecovered() {
        XCTAssertEqual(plan(["x", "a", "a"], ["w", "x", "a"]).recovered, ["a"])
    }

    func testFirstRunBaselines() {
        let p = plan(["a", "b"], ["250"], baselined: false)
        XCTAssertTrue(p.baseline); XCTAssertEqual(p.recovered, [])
        XCTAssertEqual(p.baselineHistory, ["a", "b"])
        XCTAssertEqual(plan(["a", "b", "h"], [], held: ["h"], baselined: false).baselineHistory,
                       ["a", "b"])                     // held shots get logged, not saved
    }

    func testEmptyLogBaselinesButEmptyHistoryDoesNot() {
        XCTAssertTrue(plan(["a"], []).baseline)
        XCTAssertFalse(plan([], ["a"]).baseline)
    }

    func testHeldShotsStripped() {
        XCTAssertEqual(plan(["a", "b", "c", "h1", "h2"], ["x", "a"], held: ["h1", "h2"]).recovered,
                       ["b", "c"])
        XCTAssertEqual(plan(["a", "b", "h1"], ["a"], held: ["ERR", "h1"]).recovered, ["b"])
    }

    func testHeldMismatchRecoversNothing() {
        let p = plan(["a", "b", "c"], ["a"], held: ["zz"])
        XCTAssertTrue(p.heldMismatch); XCTAssertEqual(p.recovered, [])
    }

    func testFullWithoutOverlapIsGap() {
        let p = plan(["c", "d", "e"], ["a", "b"], full: true)
        XCTAssertTrue(p.gap); XCTAssertEqual(p.recovered, ["c", "d", "e"])
        let q = plan(["c", "d"], ["a", "b"], full: false)
        XCTAssertFalse(q.gap); XCTAssertEqual(q.recovered, ["c", "d"])
    }

    func testAnchorFallbackAvoidsDuplicates() {
        let hist = ["o1", "o2", "x", "h1", "h2", "n"]
        let tail = ["o1", "o2", "h1", "h2"]
        XCTAssertEqual(overlap(hist, tail), 0)
        XCTAssertEqual(anchor(hist, tail), 5)
        let p = plan(hist, tail)
        XCTAssertEqual(p.recovered, ["n"]); XCTAssertTrue(p.gap)
    }

    func testErrorsAlignedWhenDeviceKeepsThem() {
        XCTAssertEqual(plan(["a", "ERR", "ERR", "b"], ["a", "ERR"], keepsErrors: true).recovered,
                       ["ERR", "b"])
    }
}
