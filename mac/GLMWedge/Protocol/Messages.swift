import Foundation

/// EDC measurement payload. Mirrors EDCMeasurement in glm/protocol/messages.py.
struct EDCMeasurement {
    let refEdge: UInt8
    let devMode: UInt8
    let laserOn: Bool
    let tempWarning: Bool
    let battWarning: Bool
    let configUnits: UInt8     // 0=metric, 1=imperial
    let deviceStatus: UInt8    // 4-bit error/state field
    let measId: UInt16
    let result: Float          // displayed value (meters for distance modes)
    let comp1: Float
    let comp2: Float

    var isError: Bool { devMode == DevMode.errorResponse }

    /// A history slot with no shot in it. Unused slots read back as devMode 1
    /// with all values zero. Values alone aren't enough: stored indirect
    /// measurements (devMode 17) can have a negative result.
    var isEmptySlot: Bool {
        devMode == DevMode.noAction || (result == 0 && comp1 == 0 && comp2 == 0)
    }

    init(refEdge: UInt8 = 0, devMode: UInt8, laserOn: Bool = false,
         tempWarning: Bool = false, battWarning: Bool = false,
         configUnits: UInt8 = 1, deviceStatus: UInt8 = 0, measId: UInt16,
         result: Float, comp1: Float = 0, comp2: Float = 0) {
        self.refEdge = refEdge; self.devMode = devMode; self.laserOn = laserOn
        self.tempWarning = tempWarning; self.battWarning = battWarning
        self.configUnits = configUnits; self.deviceStatus = deviceStatus
        self.measId = measId; self.result = result
        self.comp1 = comp1; self.comp2 = comp2
    }

    static func fromPayload(_ payload: Data) -> EDCMeasurement? {
        guard payload.count >= 16 else { return nil }
        let p = [UInt8](payload)
        func f32(_ i: Int) -> Float {
            Float(bitPattern: UInt32(p[i]) | UInt32(p[i + 1]) << 8
                  | UInt32(p[i + 2]) << 16 | UInt32(p[i + 3]) << 24)
        }
        return EDCMeasurement(
            refEdge: p[0] & 0x03,
            devMode: (p[0] >> 2) & 0x3F,
            laserOn: (p[1] & 0x01) != 0,
            tempWarning: (p[1] & 0x02) != 0,
            battWarning: (p[1] & 0x04) != 0,
            configUnits: (p[1] >> 3) & 0x01,
            deviceStatus: (p[1] >> 4) & 0x0F,
            measId: UInt16(p[2]) | UInt16(p[3]) << 8,
            result: f32(4), comp1: f32(8), comp2: f32(12))
    }
}

/// Enable/disable continuous measurement notifications.
func edcSetAutosync(_ on: Bool) -> Frame {
    Frame.request(cmd: Cmd.edc, payload: Data([on ? 1 : 0, 0]))
}

/// Request a stored measurement by list index (1 = newest).
/// devMode 58 = GET_LIST_ITEM_BY_INDEX; indicator 0 = the final packet.
func edcRequestHistoryItem(_ index: Int, indicator: UInt8 = 0) -> Frame {
    let headers: UInt8 = 1 | (DevMode.getListItem << 2)   // syncControl=1
    let remote: UInt8 = UInt8(index & 0x3F) | ((indicator & 0x03) << 6)
    return Frame.request(cmd: Cmd.edc, payload: Data([headers, remote]))
}

func beeperFrame(on: Bool) -> Frame {
    Frame.request(cmd: on ? Cmd.beeperOn : Cmd.beeperOff)
}
