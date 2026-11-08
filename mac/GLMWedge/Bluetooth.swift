import Foundation
import CoreBluetooth

/// CoreBluetooth link to the GLM. Mirrors glm/ble.py: find the GLM by its
/// 02a6c0… service UUID, connect, arm notifications, enable autosync, and
/// scan again whenever it drops (the GLM sleeps after a while).
///
/// The central runs on the main queue, so every callback is on the main actor.
final class GLMBluetooth: NSObject, DeviceLink {
    private let wedge: Wedge
    private var central: CBCentralManager!
    private var peripheral: CBPeripheral?
    private var characteristic: CBCharacteristic?
    private var decoder = FrameDecoder()
    private let charUUID = CBUUID(string: glmCharUUID)

    @MainActor
    init(wedge: Wedge) {
        self.wedge = wedge
        super.init()
        central = CBCentralManager(delegate: self, queue: .main)
    }

    func send(_ data: Data) {
        guard let peripheral, let characteristic else { return }
        peripheral.writeValue(data, for: characteristic, type: .withResponse)
    }

    private func onMain(_ body: @MainActor (Wedge) -> Void) {
        MainActor.assumeIsolated { body(wedge) }
    }

    private func startScan() {
        guard central.state == .poweredOn else { return }
        onMain { $0.searching() }
        central.scanForPeripherals(withServices: nil, options: nil)
    }

    private func reset() {
        peripheral = nil
        characteristic = nil
        decoder = FrameDecoder()
    }
}

extension GLMBluetooth: CBCentralManagerDelegate {
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        switch central.state {
        case .poweredOn:
            startScan()
        case .poweredOff:
            onMain { $0.bluetoothUnavailable("Bluetooth is off") }
        case .unauthorized:
            onMain { $0.bluetoothUnavailable(
                "Allow GLM Wedge in System Settings > Privacy & Security > Bluetooth") }
        case .unsupported:
            onMain { $0.bluetoothUnavailable("This Mac has no Bluetooth LE") }
        default:
            break
        }
    }

    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                        advertisementData: [String: Any], rssi RSSI: NSNumber) {
        let uuids = advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID] ?? []
        guard uuids.contains(where: { $0.uuidString.lowercased().hasPrefix(glmServicePrefix) })
        else { return }
        central.stopScan()
        self.peripheral = peripheral
        peripheral.delegate = self
        let name = peripheral.name ?? "GLM"
        onMain { $0.connecting(name) }
        central.connect(peripheral, options: nil)
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        peripheral.discoverServices(nil)
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral,
                        error: Error?) {
        reset()
        startScan()
    }

    func centralManager(_ central: CBCentralManager,
                        didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        let wasLinked = characteristic != nil
        reset()
        if wasLinked { onMain { $0.disconnected() } }
        startScan()
    }
}

extension GLMBluetooth: CBPeripheralDelegate {
    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        for svc in peripheral.services ?? []
        where svc.uuid.uuidString.lowercased().hasPrefix(glmServicePrefix) {
            peripheral.discoverCharacteristics([charUUID], for: svc)
        }
    }

    func peripheral(_ peripheral: CBPeripheral,
                    didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard let c = service.characteristics?.first(where: { $0.uuid == charUUID }) else { return }
        characteristic = c
        peripheral.setNotifyValue(true, for: c)
    }

    func peripheral(_ peripheral: CBPeripheral,
                    didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
        guard characteristic.uuid == charUUID, characteristic.isNotifying else { return }
        let name = peripheral.name ?? "GLM"
        onMain { $0.connected(device: self, name: name) }
        // Same pause as glm/ble.py before turning on autosync.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            self?.send(encode(edcSetAutosync(true)))
        }
    }

    func peripheral(_ peripheral: CBPeripheral,
                    didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard let data = characteristic.value else { return }
        let frames = decoder.feed(data)
        onMain { wedge in frames.forEach { wedge.handle(frame: $0) } }
    }
}
