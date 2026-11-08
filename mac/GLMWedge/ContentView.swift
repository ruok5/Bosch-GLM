import SwiftUI
import AppKit

struct ContentView: View {
    @EnvironmentObject private var wedge: Wedge
    @EnvironmentObject private var doorbell: Doorbell
    @State private var showActivity = false

    private let poll = Timer.publish(every: 2, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(spacing: 0) {
            StatusHeader()
            Divider()
            LastShot(line: wedge.recent.first)
            Divider()
            ShotTable(lines: wedge.recent)
            Divider()
            footer
        }
        .frame(minWidth: 480, minHeight: 420)
        .onReceive(poll) { _ in wedge.reloadIfChanged() }
        .onReceive(NotificationCenter.default.publisher(
            for: NSApplication.didBecomeActiveNotification)) { _ in doorbell.refreshTrust() }
    }

    private var footer: some View {
        HStack(spacing: 12) {
            Button { showActivity.toggle() } label: {
                if let e = wedge.events.first {
                    Label(e.text, systemImage: e.warning ? "exclamationmark.triangle.fill" : "clock")
                        .foregroundStyle(e.warning ? .orange : .secondary)
                        .lineLimit(1)
                } else {
                    Label("No activity yet", systemImage: "clock").foregroundStyle(.secondary)
                }
            }
            .buttonStyle(.plain)
            .popover(isPresented: $showActivity, arrowEdge: .bottom) { ActivityList() }

            Spacer()

            DoorbellControl()

            Button {
                NSWorkspace.shared.activateFileViewerSelecting([wedge.log.url])
            } label: {
                Image(systemName: "folder")
            }
            .help("Show shots.log in Finder")
        }
        .font(.callout)
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
    }
}

// MARK: - header

private struct StatusHeader: View {
    @EnvironmentObject private var wedge: Wedge

    var body: some View {
        HStack(spacing: 10) {
            Circle().fill(color).frame(width: 10, height: 10)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.headline)
                Text(subtitle).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 1) {
                Text("next seq").font(.caption2).foregroundStyle(.secondary)
                Text("\(wedge.nextSeq)").font(.system(.title3, design: .monospaced))
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    private var title: String {
        switch wedge.link {
        case .bluetoothOff: return "Bluetooth unavailable"
        case .searching: return "Looking for the GLM…"
        case .connecting(let n): return "Connecting to \(n)…"
        case .connected(let n): return wedge.catchingUp ? "\(n) · catching up…" : n
        }
    }

    private var subtitle: String {
        switch wedge.link {
        case .bluetoothOff(let why): return why
        case .searching: return "Wake the GLM to connect. Shots taken meanwhile are recovered."
        case .connecting: return "Found it"
        case .connected: return wedge.catchingUp ? "Reading the GLM's history" : "Connected · logging live"
        }
    }

    private var color: Color {
        switch wedge.link {
        case .bluetoothOff: return .red
        case .searching: return .secondary
        case .connecting: return .yellow
        case .connected: return wedge.catchingUp ? .yellow : .green
        }
    }
}

// MARK: - last shot

private struct LastShot: View {
    let line: LogLine?

    var body: some View {
        VStack(spacing: 4) {
            Text(big)
                .font(.system(size: 44, weight: .semibold, design: .rounded))
                .foregroundStyle(tint)
                .monospacedDigit()
            Text(small)
                .font(.callout)
                .foregroundStyle(.secondary)
                .monospacedDigit()
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 14)
    }

    private var big: String {
        guard let line else { return "–" }
        if let inches = Double(line.value) { return FeetInches.string(inches) }
        return line.value
    }

    private var small: String {
        guard let line else { return "No shots logged yet" }
        let value = Double(line.value) != nil ? "\(line.value) in" : line.value
        return "\(value) · seq \(line.seq) · \(line.kind) · \(ShotTable.clock(line.time))"
    }

    private var tint: Color { line.map { ShotTable.color(for: $0) } ?? .secondary }
}

// MARK: - table

private struct ShotTable: View {
    let lines: [LogLine]

    var body: some View {
        Table(lines) {
            TableColumn("Seq") { Text("\($0.seq)").monospacedDigit() }
                .width(min: 40, ideal: 50, max: 70)
            TableColumn("Time") { Text(Self.clock($0.time)).monospacedDigit() }
                .width(min: 60, ideal: 70, max: 90)
            TableColumn("Inches") { l in
                Text(l.value).monospacedDigit().foregroundStyle(Self.color(for: l))
            }
            .width(min: 70, ideal: 90)
            TableColumn("Ft-in") { l in
                Text(Double(l.value).map { FeetInches.string($0) } ?? "")
                    .monospacedDigit().foregroundStyle(Self.color(for: l))
            }
            .width(min: 70, ideal: 90)
            TableColumn("Source") { l in
                Text(l.kind).foregroundStyle(l.kind == Shot.live ? .primary : .secondary)
            }
            .width(min: 60, ideal: 80)
        }
    }

    static func clock(_ stamp: String) -> String {
        stamp.split(separator: "T").last.map(String.init) ?? stamp
    }

    static func color(for line: LogLine) -> Color {
        switch line.value {
        case Shot.err: return .red
        case Shot.gap: return .orange
        default: return .primary
        }
    }
}

// MARK: - footer pieces

private struct DoorbellControl: View {
    @EnvironmentObject private var doorbell: Doorbell

    var body: some View {
        HStack(spacing: 6) {
            Toggle("Doorbell", isOn: $doorbell.enabled)
                .toggleStyle(.switch)
                .controlSize(.mini)
                .help("Nudge the mouse one pixel after each shot so AutoCAD's LASER command wakes up")
            if doorbell.enabled && !doorbell.trusted {
                Button("Allow…") { doorbell.requestTrust() }
                    .controlSize(.small)
                    .help("The doorbell needs Accessibility permission")
            }
        }
    }
}

private struct ActivityList: View {
    @EnvironmentObject private var wedge: Wedge

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Activity").font(.headline)
            if wedge.events.isEmpty {
                Text("Nothing yet").foregroundStyle(.secondary)
            }
            ForEach(wedge.events) { e in
                HStack(alignment: .firstTextBaseline) {
                    Text(e.time, format: .dateTime.hour().minute().second())
                        .monospacedDigit().foregroundStyle(.secondary)
                    Text(e.text).foregroundStyle(e.warning ? .orange : .primary)
                }
                .font(.callout)
            }
        }
        .padding(12)
        .frame(width: 420, alignment: .leading)
    }
}

struct SettingsView: View {
    @EnvironmentObject private var wedge: Wedge
    @EnvironmentObject private var doorbell: Doorbell
    @AppStorage("notifyURL") private var notifyURL = ""

    var body: some View {
        Form {
            Toggle("Doorbell: wake AutoCAD after each shot", isOn: $doorbell.enabled)
            LabeledContent("Accessibility") {
                if doorbell.trusted {
                    Text("Allowed").foregroundStyle(.secondary)
                } else {
                    Button("Allow…") { doorbell.requestTrust() }
                }
            }
            TextField("Notify URL", text: $notifyURL, prompt: Text("https://ntfy.sh/your-topic"))
            Text("Connection changes are POSTed here as plain text (ntfy style). Leave empty for none.")
                .font(.caption).foregroundStyle(.secondary)
            LabeledContent("Log file", value: wedge.log.url.path)
        }
        .padding(20)
        .frame(width: 460)
    }
}

enum FeetInches {
    /// 124.5 → 10'-4 1/2", to the nearest 1/8".
    static func string(_ inches: Double, denominator: Int = 8) -> String {
        let units = Int((inches * Double(denominator)).rounded())
        let perFoot = 12 * denominator
        let feet = units / perFoot
        let rem = units % perFoot
        let whole = rem / denominator
        var num = rem % denominator
        var den = denominator
        while num > 0 && num % 2 == 0 { num /= 2; den /= 2 }
        return num == 0 ? "\(feet)'-\(whole)\"" : "\(feet)'-\(whole) \(num)/\(den)\""
    }
}
