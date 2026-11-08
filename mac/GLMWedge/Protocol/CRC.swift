import Foundation

enum CRC {
    static let crc8Init: UInt8  = 0xAA
    static let crc8Poly: UInt8  = 0xA6
    static let crc16Init: UInt16 = 0xAAAA
    static let crc16Poly: UInt16 = 0xBAAD

    static func crc8(_ data: Data) -> UInt8 {
        var reg = crc8Init
        for byte in data {
            for i in 0..<8 {
                let top = (reg & 0x80) != 0
                let bit = ((byte >> UInt8(7 - i)) & 1) == 1
                reg = top != bit ? (reg &<< 1) ^ crc8Poly : reg &<< 1
            }
        }
        return reg
    }

    static func crc16(_ data: Data) -> UInt16 {
        var reg = crc16Init
        for byte in data {
            for i in 0..<8 {
                let top = (reg & 0x8000) != 0
                let bit = ((byte >> UInt8(7 - i)) & 1) == 1
                reg = top != bit ? (reg &<< 1) ^ crc16Poly : reg &<< 1
            }
        }
        return reg
    }
}
