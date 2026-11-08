import Foundation

// The shot log LASER.lsp reads: ~/.glm/shots.log.
//
// Contract (owned jointly with the autolisp session; changes go there first):
//
//     seq <TAB> YYYY-MM-DDTHH:MM:SS <TAB> inches <TAB> live|recovered [<TAB> meas_id]
//
// - Append-only, one line per measurement, flushed per line, never rewritten.
// - seq is monotonic across restarts: kept in wedge.json next to the log and
//   re-read from the log's last line, taking the larger.
// - inches has 4 decimals. An error shot is the literal ERR, never dropped or
//   collapsed: LASER reads two adjacent ERR lines as a reset, by file order
//   alone, on live and recovered lines alike.
// - GAP (source recovered) marks shots lost off the end of the device's
//   history ring. Written ahead of that catch-up's recovered lines; consumes
//   a seq; LASER treats it as a reset point.
// - No comment lines. meas_id only on live lines.

enum Shot {
    static let err = "ERR"
    static let gap = "GAP"
    static let live = "live"
    static let recovered = "recovered"
    static let inchesPerMeter = 39.3700787
}

/// The value column for a measurement: ERR, inches to 4 places, or nil for
/// anything that isn't a logged shot. Only single distance is logged: area,
/// volume, angle and indirect modes aren't a length, and continuous distance
/// is skipped until it's checked on the device.
func classify(_ m: EDCMeasurement) -> String? {
    if m.isError { return Shot.err }
    guard m.devMode == DevMode.singleDistance, m.result > 0 else { return nil }
    return String(format: "%.4f", Double(m.result) * Shot.inchesPerMeter)
}

private let stampFormatter: DateFormatter = {
    let f = DateFormatter()
    f.locale = Locale(identifier: "en_US_POSIX")
    f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
    return f
}()

func formatLine(seq: Int, when: Date, value: String, kind: String,
                measId: Int? = nil) -> String {
    var cols = [String(seq), stampFormatter.string(from: when), value, kind]
    if let measId { cols.append(String(measId)) }
    return cols.joined(separator: "\t") + "\n"
}

struct LogLine: Identifiable, Equatable {
    let index: Int          // line position in the file; seq may repeat in a hand-made log
    let seq: Int
    let time: String
    let value: String
    let kind: String
    var id: Int { index }
}

func parseLine(_ line: Substring, index: Int = 0) -> LogLine? {
    let cols = line.split(separator: "\t", omittingEmptySubsequences: false)
    guard cols.count >= 4, let seq = Int(cols[0]) else { return nil }
    return LogLine(index: index, seq: seq, time: String(cols[1]),
                   value: String(cols[2]), kind: String(cols[3]))
}

final class ShotLog {
    static let defaultDirectory = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".glm", isDirectory: true)

    let directory: URL
    let url: URL
    let stateURL: URL
    private(set) var seq: Int
    private var state: [String: Any]

    init(directory: URL = ShotLog.defaultDirectory) {
        self.directory = directory
        url = directory.appendingPathComponent("shots.log")
        stateURL = directory.appendingPathComponent("wedge.json")
        try? FileManager.default.createDirectory(at: directory,
                                                 withIntermediateDirectories: true)
        state = (try? Data(contentsOf: stateURL))
            .flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
        seq = 0
        let stateSeq = state["seq"] as? Int ?? 0
        let logSeq = lines().map(\.seq).max() ?? 0
        seq = max(stateSeq, logSeq)
    }

    // MARK: state

    /// False until the wedge has seen this device's history once. The first
    /// catch-up only records a baseline: it backfills nothing, but saves what
    /// was on the device so the next catch-up has something to line up with.
    var baselined: Bool { baseline != nil }

    /// The device history at baseline (oldest first) and the log seq then.
    var baseline: (seq: Int, history: [String])? {
        guard let b = state["baseline"] as? [String: Any],
              let seq = b["seq"] as? Int, let history = b["history"] as? [String]
        else { return nil }
        return (seq, history)
    }

    func markBaselined(history: [String] = []) {
        state["baseline"] = ["seq": seq, "history": history]
        saveState()
    }

    private func saveState() {
        state["seq"] = seq
        guard let data = try? JSONSerialization.data(withJSONObject: state) else { return }
        try? data.write(to: stateURL, options: .atomic)
    }

    // MARK: reading

    func lines() -> [LogLine] {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return [] }
        return text.split(separator: "\n").enumerated()
            .compactMap { parseLine($0.element, index: $0.offset) }
    }

    /// The last n shots the device is known to have, oldest first: the
    /// history saved at baseline, then every line logged since. Lines from
    /// before the baseline weren't device shots as far as alignment goes.
    /// GAP lines are markers, not device shots; ERR lines are left out when
    /// the device doesn't keep errors in its history, so the tail lines up
    /// with a history read. An empty log (moved aside) gives an empty tail,
    /// which makes the next catch-up take a fresh baseline.
    func tailValues(_ n: Int, includeErrors: Bool) -> [String] {
        guard n > 0 else { return [] }
        let all = lines()
        guard !all.isEmpty else { return [] }
        var values: [String] = []
        var since = Int.min
        if let b = baseline {
            values = b.history
            since = b.seq
        }
        values += all.filter { $0.seq > since }.map(\.value)
        values = values.filter { $0 != Shot.gap && (includeErrors || $0 != Shot.err) }
        return Array(values.suffix(n))
    }

    // MARK: writing

    @discardableResult
    func append(_ value: String, kind: String, measId: Int? = nil,
                when: Date = Date()) -> Int {
        seq += 1
        let line = formatLine(seq: seq, when: when, value: value, kind: kind, measId: measId)
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: nil)
        }
        if let h = try? FileHandle(forWritingTo: url) {
            h.seekToEndOfFile()
            h.write(Data(line.utf8))
            try? h.synchronize()
            try? h.close()
        }
        saveState()
        return seq
    }
}

// MARK: - catch-up alignment

/// Length of the longest run where the oldest end of `history` equals the
/// newest end of `tail` (both oldest first). Everything in history past that
/// run is new. Aligns by sequence, not value membership, so a deliberately
/// repeated reading (the same wall shot twice) is still recovered.
func overlap(_ history: [String], _ tail: [String]) -> Int {
    var j = min(history.count, tail.count)
    while j > 0 {
        if Array(history.prefix(j)) == Array(tail.suffix(j)) { return j }
        j -= 1
    }
    return 0
}

/// Fallback when `overlap` finds nothing: locate the log's newest run of at
/// least `minRun` values anywhere in history and return the index just past
/// it (0 if not found). Only happens when the log fell out of device order;
/// whatever sits before the anchor can't be recovered in order, and
/// recovering it would duplicate shots.
func anchor(_ history: [String], _ tail: [String], minRun: Int = 2) -> Int {
    var k = min(tail.count, history.count)
    while k >= minRun {
        let run = Array(tail.suffix(k))
        var p = history.count - k
        while p >= 0 {
            if Array(history[p..<(p + k)]) == run { return p + k }
            p -= 1
        }
        k -= 1
    }
    return 0
}

struct CatchupPlan: Equatable {
    var recovered: [String] = []   // oldest first
    var overlap = 0
    var baseline = false           // first sight of the device: nothing backfilled
    var baselineHistory: [String] = []   // what to save as seen (oldest first)
    var gap = false                // shots were lost: write GAP ahead of recovered
    var heldMismatch = false       // held live shots not found at history's end
}

/// Decide which history entries to append as `recovered`.
/// - historyNewestFirst: classified values in device order (index 1 first).
/// - tail: log tail, oldest first, filtered like the history.
/// - heldLive: live shots that arrived while catch-up ran. They're already at
///   the newest end of the device history but not in the log yet; they get
///   logged as `live` after the recovered lines, so strip them here.
/// - full: the device history was at capacity.
func planCatchup(historyNewestFirst: [String], tail: [String], heldLive: [String],
                 keepsErrors: Bool, full: Bool, baselined: Bool) -> CatchupPlan {
    var hist = Array(historyNewestFirst.reversed())
    var plan = CatchupPlan()

    let held = heldLive.filter { keepsErrors || $0 != Shot.err }
    if !held.isEmpty {
        if Array(hist.suffix(held.count)) == held {
            hist.removeLast(held.count)
        } else {
            // Can't tell held shots from offline ones; recovering anything
            // risks a duplicate. Skip this round; the shots stay on the device.
            plan.heldMismatch = true
            return plan
        }
    }

    if !baselined || (tail.isEmpty && !hist.isEmpty) {
        // First sight of the device, or the log was moved aside: take what's
        // on the device as already seen.
        plan.baseline = true
        plan.baselineHistory = hist
        return plan
    }
    if hist.isEmpty { return plan }

    let j = overlap(hist, tail)
    plan.overlap = j
    if j > 0 {
        plan.recovered = Array(hist[j...])
        return plan
    }
    let p = anchor(hist, tail)
    if p > 0 {
        plan.recovered = Array(hist[p...])
        plan.gap = true
        return plan
    }
    // Nothing in common. A full history means more shots were taken away
    // from the laptop than the device keeps; otherwise it was cleared.
    plan.recovered = hist
    plan.gap = full
    return plan
}
