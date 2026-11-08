import Foundation

// Bosch MtProtocol constants. Mirrors glm/protocol/constants.py.

enum FrameFormat: UInt8 {
    case long  = 0
    case short = 1
    case ext   = 2
}

enum FrameType {
    case request
    case response
}

enum CommStatus: UInt8 {
    case success           = 0
    case timeout           = 1
    case modeNotSupported  = 2
    case checksumError     = 3
    case cmdUnknown        = 4
    case accessDenied      = 5
    case paramOrDataError  = 6
}

func frameMode(req: FrameFormat, resp: FrameFormat) -> UInt8 {
    return 0xC0 | ((req.rawValue & 3) << 2) | (resp.rawValue & 3)
}

func splitMode(_ mode: UInt8) -> (FrameFormat, FrameFormat) {
    let req  = FrameFormat(rawValue: (mode >> 2) & 3) ?? .long
    let resp = FrameFormat(rawValue:  mode       & 3) ?? .long
    return (req, resp)
}

enum Cmd {
    static let edc: UInt8         = 0x55
    static let getSettings: UInt8 = 0x53
    static let setSettings: UInt8 = 0x54
    // Undocumented, found by probing the GLM165 (see glm/feedback.py).
    static let beeperOn: UInt8    = 0x45
    static let beeperOff: UInt8   = 0x46
}

enum DevMode {
    static let noAction: UInt8       = 0
    static let singleDistance: UInt8 = 1
    static let getListItem: UInt8    = 58
    static let errorResponse: UInt8  = 63
}

/// GLM Bluetooth GATT characteristic carrying the framed protocol.
let glmCharUUID = "02a6c0d1-0451-4000-b000-fb3210111989"
let glmServicePrefix = "02a6c0"
