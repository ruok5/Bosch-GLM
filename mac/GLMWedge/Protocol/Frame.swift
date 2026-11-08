import Foundation

struct Frame {
    var type: FrameType
    var cmd: UInt8
    var payload: Data
    var status: CommStatus
    var reqFmt: FrameFormat
    var respFmt: FrameFormat

    static func request(cmd: UInt8,
                        payload: Data = Data(),
                        reqFmt: FrameFormat = .long,
                        respFmt: FrameFormat = .long) -> Frame {
        Frame(type: .request, cmd: cmd, payload: payload,
              status: .success, reqFmt: reqFmt, respFmt: respFmt)
    }
}

func encode(_ frame: Frame) -> Data {
    switch frame.type {
    case .request:
        let head = Data([frameMode(req: frame.reqFmt, resp: frame.respFmt), frame.cmd])
        return wrap(head: head, payload: frame.payload, fmt: frame.reqFmt)
    case .response:
        var head = Data([frame.status.rawValue])
        if frame.reqFmt == .ext {
            head.append(frame.cmd)
        }
        return wrap(head: head, payload: frame.payload, fmt: frame.reqFmt)
    }
}

private func wrap(head: Data, payload: Data, fmt: FrameFormat) -> Data {
    switch fmt {
    case .short:
        var body = head
        body.append(CRC.crc8(body))
        return body
    case .long:
        var body = head
        body.append(UInt8(payload.count & 0xFF))
        body.append(payload)
        body.append(CRC.crc8(body))
        return body
    case .ext:
        let n = payload.count
        var body = head
        body.append(UInt8(n & 0xFF))
        body.append(UInt8((n >> 8) & 0xFF))
        body.append(payload)
        let c = CRC.crc16(body)
        body.append(UInt8(c & 0xFF))
        body.append(UInt8((c >> 8) & 0xFF))
        return body
    }
}

/// Incremental decoder. Append bytes; pull completed Frames.
final class FrameDecoder {
    private var buf = Data()

    func feed(_ data: Data) -> [Frame] {
        buf.append(data)
        var out: [Frame] = []
        while let (frame, consumed) = tryParse() {
            if consumed > 0 {
                buf.removeFirst(consumed)
            }
            if let frame { out.append(frame) }
            if consumed == 0 { break }
        }
        return out
    }

    /// Returns `(frame, consumed)`. `frame == nil, consumed == 0` means need more bytes.
    /// `frame == nil, consumed == 1` means resync by dropping a byte.
    private func tryParse() -> (Frame?, Int)? {
        guard let first = buf.first else { return nil }
        let isRequest = (first & 0xF0) == 0xC0
        return isRequest ? parseRequest() : parseResponse()
    }

    private func parseRequest() -> (Frame?, Int)? {
        guard buf.count >= 2 else { return (nil, 0) }
        let mode = buf[buf.startIndex]
        let cmd  = buf[buf.startIndex + 1]
        let (reqFmt, respFmt) = splitMode(mode)
        return parseBody(headLen: 2, fmt: reqFmt) { payload in
            Frame(type: .request, cmd: cmd, payload: payload,
                  status: .success, reqFmt: reqFmt, respFmt: respFmt)
        }
    }

    private func parseResponse() -> (Frame?, Int)? {
        guard !buf.isEmpty else { return (nil, 0) }
        let statusByte = buf[buf.startIndex]
        guard let status = CommStatus(rawValue: statusByte) else {
            return (nil, 1)
        }
        return parseBody(headLen: 1, fmt: .long) { payload in
            Frame(type: .response, cmd: 0, payload: payload,
                  status: status, reqFmt: .long, respFmt: .long)
        }
    }

    private func parseBody(headLen: Int, fmt: FrameFormat,
                           build: (Data) -> Frame) -> (Frame?, Int)? {
        let b = buf
        let base = b.startIndex
        switch fmt {
        case .short:
            let total = headLen + 1
            guard b.count >= total else { return (nil, 0) }
            let body = b[base..<(base + headLen)]
            if CRC.crc8(Data(body)) != b[base + headLen] { return (nil, 1) }
            return (build(Data()), total)
        case .long:
            guard b.count >= headLen + 1 else { return (nil, 0) }
            let n = Int(b[base + headLen])
            let total = headLen + 1 + n + 1
            guard b.count >= total else { return (nil, 0) }
            let body = b[base..<(base + headLen + 1 + n)]
            if CRC.crc8(Data(body)) != b[base + headLen + 1 + n] { return (nil, 1) }
            let payload = b[(base + headLen + 1)..<(base + headLen + 1 + n)]
            return (build(Data(payload)), total)
        case .ext:
            guard b.count >= headLen + 2 else { return (nil, 0) }
            let n = Int(b[base + headLen]) | (Int(b[base + headLen + 1]) << 8)
            let total = headLen + 2 + n + 2
            guard b.count >= total else { return (nil, 0) }
            let body = b[base..<(base + headLen + 2 + n)]
            let expected = CRC.crc16(Data(body))
            let actual = UInt16(b[base + headLen + 2 + n]) |
                         (UInt16(b[base + headLen + 2 + n + 1]) << 8)
            if expected != actual { return (nil, 1) }
            let payload = b[(base + headLen + 2)..<(base + headLen + 2 + n)]
            return (build(Data(payload)), total)
        }
    }
}
