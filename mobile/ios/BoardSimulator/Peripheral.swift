import Foundation
import CoreBluetooth
import UIKit

final class Peripheral: NSObject, CBPeripheralManagerDelegate {
    private weak var controller: BoardController?
    private let slot: Int
    private var manager: CBPeripheralManager!
    private var pending: [String: Any]?
    private var profile: [String: Any]?
    private var chars: [CBUUID: CBMutableCharacteristic] = [:]
    private var values: [CBUUID: Data] = [:]
    private var services: [CBMutableService] = []
    private struct Notification { let value: Data; let recipient: UUID? }
    private var notifications: [Notification] = []
    private var multi = false
    private var writers: Set<UUID> = []
    private var subscribers: [UUID: CBCentral] = [:]
    private var owner: UUID?
    private var running = false
    private var publishing: CBMutableService?
    private var epoch = 0
    private let stateUUID = CBUUID(string: "FFF4")
    private let notifyUUID = CBUUID(string: "FFF1")

    init(controller: BoardController, slot: Int) {
        self.controller = controller; self.slot = slot
        super.init()
        // Defer permission prompt until the user starts BLE.
    }
    private func status(_ message: String, running: Bool) { controller?.status(message, running: running, slot: slot) }
    func start(_ profile: [String: Any], multi: Bool) {
        stop(); self.multi = multi
        guard UIApplication.shared.applicationState == .active else {
            status("Return to foreground, then Start", running: false); return
        }
        pending = profile
        if manager == nil { manager = CBPeripheralManager(delegate: self, queue: .main) }
        else { peripheralManagerDidUpdateState(manager) }
    }
    func stop() {
        epoch += 1; running = false; pending = nil; profile = nil; publishing = nil
        manager?.stopAdvertising(); manager?.removeAllServices()
        chars.removeAll(); values.removeAll(); services.removeAll()
        notifications.removeAll(); subscribers.removeAll(); writers.removeAll(); owner = nil
    }
    func setMulti(_ enabled: Bool) {
        multi = enabled
        guard running, publishing == nil, services.isEmpty else { return }
        if !multi && owner != nil { manager.stopAdvertising() }
        else { publishNext() }
        status(enabled ? "Multi-connect: independent transport buffers per controller" : "Exclusive: advertising stops at first observable ATT activity; resume manually after disconnect", running: true)
    }
    private func observe(_ central: CBCentral) {
        writers.insert(central.identifier)
        if owner == nil { owner = central.identifier }
        if !multi {
            manager.stopAdvertising()
            status("Exclusive: advertising stopped after ATT activity. CoreBluetooth has no UART disconnect event; disconnect, then Resume advertising.", running: true)
        }
    }
    func releaseController() {
        guard running else { return }
        for writer in writers { controller?.disconnected(writer, slot: slot) }
        writers.removeAll(); owner = nil
        publishNext()
    }
    private func fail(_ message: String) {
        stop(); status("BLE error: " + message, running: false)
    }
    func peripheralManagerDidUpdateState(_ peripheral: CBPeripheralManager) {
        guard peripheral.state == .poweredOn else {
            if peripheral.state == .unknown || peripheral.state == .resetting {
                status("Waiting for Bluetooth state", running: pending != nil); return
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
        if multi || owner == nil {
            manager.startAdvertising([CBAdvertisementDataLocalNameKey: name,
                                      CBAdvertisementDataServiceUUIDsKey: [CBUUID(string: advertised)]])
        }
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
        else { status("Advertising \(profile?["name"] as? String ?? "board") · name/UUID delivery is controlled by iOS", running: true) }
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
            if !multi, let owner = owner, owner != request.central.identifier, !writers.contains(request.central.identifier) {
                peripheral.respond(to: first, withResult: .unlikelyError)
                status("Another controller owns this run. Stop/Start to change controller.", running: true); return
            }
        }
        observe(first.central)
        let generation = epoch
        for request in requests {
            controller?.receive(request.value ?? Data(), peer: request.central.identifier, token: profile?["runToken"] as? Int ?? 0, slot: slot) { updates in
                guard self.epoch == generation, self.running else { return }
                for update in updates {
                    if let value = update["state"] as? [UInt8] { self.values[self.stateUUID] = Data(value) }
                    if let value = update["notify"] as? [UInt8], !self.subscribers.isEmpty {
                        guard self.notifications.count < 128 else { self.fail("Notification queue overflow"); return }
                        self.notifications.append(Notification(value: Data(value), recipient: value.count > 1 && value[1] & 0x80 != 0 ? request.central.identifier : nil))
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
        if !multi, let owner = owner, owner != request.central.identifier, !writers.contains(request.central.identifier) {
            peripheral.respond(to: request, withResult: .unlikelyError); return
        }
        observe(request.central)
        let generation = epoch
        let respond: (Data) -> Void = { value in
            guard self.epoch == generation else { return }
            guard request.offset >= 0, request.offset <= value.count else { peripheral.respond(to: request, withResult: .invalidOffset); return }
            request.value = value.subdata(in: request.offset..<value.count)
            peripheral.respond(to: request, withResult: .success)
        }
        if request.characteristic.uuid == stateUUID { controller?.readState(slot: slot, respond) }
        else { respond(values[request.characteristic.uuid] ?? Data()) }
    }
    func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral, didSubscribeTo characteristic: CBCharacteristic) {
        guard running, characteristic.uuid == notifyUUID else { return }
        if !multi, let owner = owner, owner != central.identifier, !writers.contains(central.identifier) { return }
        subscribers[central.identifier] = central
        observe(central)
        status("Quantum notifications subscribed · maximum \(central.maximumUpdateValueLength) bytes", running: true)
    }
    func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral, didUnsubscribeFrom characteristic: CBCharacteristic) {
        subscribers.removeValue(forKey: central.identifier)
        // Unsubscribe is not proof of link loss; never unlock exclusive mode here.
        controller?.disconnected(central.identifier, slot: slot)
        if subscribers.isEmpty { notifications.removeAll() }
        status("Unsubscribed; disconnect the controller, then use Resume advertising if exclusive", running: true)
    }
    func peripheralManagerIsReady(toUpdateSubscribers peripheral: CBPeripheralManager) { flush() }
    private func flush() {
        guard running, let c = chars[notifyUUID] else { return }
        while let notification = notifications.first {
            let value = notification.value
            let targets = subscribers.values.filter { notification.recipient == nil || $0.identifier == notification.recipient }
            guard !targets.isEmpty else { notifications.removeFirst(); continue }
            if targets.contains(where: { value.count > $0.maximumUpdateValueLength }) {
                notifications.removeFirst()
                status("Quantum broadcast exceeds negotiated notification size (\(value.count) bytes); fff4 remains readable", running: true)
                continue
            }
            guard manager.updateValue(value, for: c, onSubscribedCentrals: targets) else { return }
            notifications.removeFirst()
        }
    }
}
