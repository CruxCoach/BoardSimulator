import Foundation
import CoreBluetooth
import UIKit

final class Peripheral: NSObject, CBPeripheralManagerDelegate {
    private weak var controller: BoardController?
    private var manager: CBPeripheralManager!
    private var pending: [String: Any]?
    private var profile: [String: Any]?
    private var chars: [CBUUID: CBMutableCharacteristic] = [:]
    private var values: [CBUUID: Data] = [:]
    private var services: [CBMutableService] = []
    private var notifications: [Data] = []
    private var subscribers: [UUID: CBCentral] = [:]
    private var owner: UUID?
    private var running = false
    private var publishing: CBMutableService?
    private var epoch = 0
    private let stateUUID = CBUUID(string: "FFF4")
    private let notifyUUID = CBUUID(string: "FFF1")

    init(controller: BoardController) {
        self.controller = controller
        super.init()
        // Defer permission prompt until the user starts BLE.
    }
    func start(_ profile: [String: Any]) {
        stop()
        guard UIApplication.shared.applicationState == .active else {
            controller?.status("Return to foreground, then Start", running: false); return
        }
        pending = profile
        if manager == nil { manager = CBPeripheralManager(delegate: self, queue: .main) }
        else { peripheralManagerDidUpdateState(manager) }
    }
    func stop() {
        epoch += 1; running = false; pending = nil; profile = nil; publishing = nil
        manager?.stopAdvertising(); manager?.removeAllServices()
        chars.removeAll(); values.removeAll(); services.removeAll()
        notifications.removeAll(); subscribers.removeAll(); owner = nil
    }
    private func fail(_ message: String) {
        stop(); controller?.status("BLE error: " + message, running: false)
    }
    func peripheralManagerDidUpdateState(_ peripheral: CBPeripheralManager) {
        guard peripheral.state == .poweredOn else {
            if peripheral.state == .unknown || peripheral.state == .resetting {
                controller?.status("Waiting for Bluetooth state", running: pending != nil); return
            }
            let reason: String
            switch peripheral.state {
            case .unauthorized: reason = "Bluetooth permission denied. Allow Bluetooth in Settings."
            case .unsupported: reason = "BLE peripheral role unavailable on this device."
            case .poweredOff: reason = "Bluetooth is off. Enable it, then Start."
            default: reason = "Bluetooth unavailable (\(peripheral.state.rawValue))"
            }
            fail(reason); return
        }
        guard let selected = pending else { return }
        pending = nil; profile = selected
        guard let specs = selected["services"] as? [[String: Any]] else { fail("Invalid GATT profile"); return }
        for spec in specs {
            guard let uuid = spec["uuid"] as? String else { fail("Missing service UUID"); return }
            let service = CBMutableService(type: CBUUID(string: uuid), primary: true)
            var attributes: [CBMutableCharacteristic] = []
            for entry in spec["characteristics"] as? [[String: Any]] ?? [] {
                guard let uuid = entry["uuid"] as? String, let flags = entry["flags"] as? [String] else { fail("Invalid characteristic"); return }
                var properties: CBCharacteristicProperties = [], permissions: CBAttributePermissions = []
                if flags.contains("read") { properties.insert(.read); permissions.insert(.readable) }
                if flags.contains("write") { properties.insert(.write); permissions.insert(.writeable) }
                if flags.contains("write-without-response") { properties.insert(.writeWithoutResponse); permissions.insert(.writeable) }
                if flags.contains("notify") { properties.insert(.notify) }
                let id = CBUUID(string: uuid)
                let c = CBMutableCharacteristic(type: id, properties: properties, value: nil, permissions: permissions)
                values[id] = Data(entry["value"] as? [UInt8] ?? [])
                chars[id] = c; attributes.append(c)
            }
            service.characteristics = attributes; services.append(service)
        }
        running = true; publishNext()
    }
    private func publishNext() {
        if !services.isEmpty {
            let service = services.removeFirst(); publishing = service; manager.add(service); return
        }
        guard let profile = profile, let name = profile["name"] as? String,
              let advertised = profile["advertised"] as? String else { fail("Invalid identity"); return }
        manager.startAdvertising([CBAdvertisementDataLocalNameKey: name,
                                  CBAdvertisementDataServiceUUIDsKey: [CBUUID(string: advertised)]])
    }
    func peripheralManager(_ peripheral: CBPeripheralManager, didAdd service: CBService, error: Error?) {
        guard running, service === publishing else { return }
        publishing = nil
        if let error = error { fail("Service registration: \(error.localizedDescription)") }
        else { publishNext() }
    }
    func peripheralManagerDidStartAdvertising(_ peripheral: CBPeripheralManager, error: Error?) {
        guard running else { return }
        if let error = error { fail("Advertising: \(error.localizedDescription)") }
        else { controller?.status("Advertising \(profile?["name"] as? String ?? "board") · name/UUID delivery is controlled by iOS", running: true) }
    }
    func peripheralManager(_ peripheral: CBPeripheralManager, didReceiveWrite requests: [CBATTRequest]) {
        guard let first = requests.first else { return }
        guard running else { peripheral.respond(to: first, withResult: .unlikelyError); return }
        // CoreBluetooth has no disconnect callback for unsubscribed UART writers.
        // Lock a run to one controller until Stop/Start, never interleave streams.
        for request in requests {
            guard request.offset == 0 else { peripheral.respond(to: first, withResult: .invalidOffset); return }
            guard let characteristic = chars[request.characteristic.uuid],
                  characteristic.properties.contains(.write) || characteristic.properties.contains(.writeWithoutResponse) else {
                peripheral.respond(to: first, withResult: .writeNotPermitted); return
            }
            if let owner = owner, owner != request.central.identifier {
                peripheral.respond(to: first, withResult: .unlikelyError)
                controller?.status("Another controller owns this run. Stop/Start to change controller.", running: true); return
            }
        }
        owner = first.central.identifier
        let generation = epoch
        for request in requests {
            controller?.receive(request.value ?? Data()) { updates in
                guard self.epoch == generation, self.running else { return }
                for update in updates {
                    if let value = update["state"] as? [UInt8] { self.values[self.stateUUID] = Data(value) }
                    if let value = update["notify"] as? [UInt8], !self.subscribers.isEmpty {
                        guard self.notifications.count < 128 else { self.fail("Notification queue overflow"); return }
                        self.notifications.append(Data(value))
                    }
                }
                self.flush()
            }
        }
        peripheral.respond(to: first, withResult: .success)
    }
    func peripheralManager(_ peripheral: CBPeripheralManager, didReceiveRead request: CBATTRequest) {
        guard running, let c = chars[request.characteristic.uuid], c.properties.contains(.read) else {
            peripheral.respond(to: request, withResult: .readNotPermitted); return
        }
        let generation = epoch
        let respond: (Data) -> Void = { value in
            guard self.epoch == generation else { return }
            guard request.offset >= 0, request.offset <= value.count else { peripheral.respond(to: request, withResult: .invalidOffset); return }
            request.value = value.subdata(in: request.offset..<value.count)
            peripheral.respond(to: request, withResult: .success)
        }
        if request.characteristic.uuid == stateUUID { controller?.readState(respond) }
        else { respond(values[request.characteristic.uuid] ?? Data()) }
    }
    func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral, didSubscribeTo characteristic: CBCharacteristic) {
        guard running, characteristic.uuid == notifyUUID else { return }
        subscribers[central.identifier] = central
        controller?.status("Quantum notifications subscribed · maximum \(central.maximumUpdateValueLength) bytes", running: true)
    }
    func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral, didUnsubscribeFrom characteristic: CBCharacteristic) {
        subscribers.removeValue(forKey: central.identifier)
        if owner == central.identifier { owner = nil; controller?.disconnected() }
        if subscribers.isEmpty { notifications.removeAll() }
    }
    func peripheralManagerIsReady(toUpdateSubscribers peripheral: CBPeripheralManager) { flush() }
    private func flush() {
        guard running, let c = chars[notifyUUID] else { return }
        while let value = notifications.first {
            let targets = Array(subscribers.values)
            guard !targets.isEmpty else { notifications.removeAll(); return }
            if targets.contains(where: { value.count > $0.maximumUpdateValueLength }) {
                notifications.removeFirst()
                controller?.status("Quantum broadcast exceeds negotiated notification size (\(value.count) bytes); fff4 remains readable", running: true)
                continue
            }
            guard manager.updateValue(value, for: c, onSubscribedCentrals: targets) else { return }
            notifications.removeFirst()
        }
    }
}
