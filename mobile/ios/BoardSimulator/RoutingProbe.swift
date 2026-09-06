import UIKit
import CoreBluetooth

/// User-operated test of two peripheral managers. Successful advertisement
/// callbacks are not evidence of separate over-the-air addresses or routing.
final class RoutingProbeController: UIViewController, CBPeripheralManagerDelegate {
    private var managers: [CBPeripheralManager] = []
    private var profiles: [[String: Any]] = []
    private var queues: [[CBMutableService]] = [[], []]
    private var values: [[CBUUID: Data]] = [[:], [:]]
    private let output = UITextView()
    private var active = false
    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        let stack = UIStackView(); stack.axis = .vertical; stack.spacing = 10
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 12),
            stack.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -12),
            stack.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 12),
            stack.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -12)])
        let label = UILabel(); label.numberOfLines = 0
        label.text = "Routing experiment: two real CoreBluetooth managers. Test discovery and writes from another device. Not a multi-board parity claim. No radio activity until Start."
        stack.addArrangedSubview(label)
        for (title, action) in [("Start duplicate UART", #selector(duplicate)), ("Start disjoint GATT", #selector(disjoint)), ("Stop probe", #selector(stop)), ("Close", #selector(close))] {
            let button = UIButton(type: .system); button.setTitle(title, for: .normal); button.addTarget(self, action: action, for: .touchUpInside); stack.addArrangedSubview(button)
        }
        output.isEditable = false; stack.addArrangedSubview(output)
        NotificationCenter.default.addObserver(self, selector: #selector(stop), name: UIApplication.didEnterBackgroundNotification, object: nil)
    }
    @objc private func duplicate() { start(disjoint: false) }
    @objc private func disjoint() { start(disjoint: true) }
    @objc private func close() { stop(); dismiss(animated: true) }
    @objc private func stop() {
        active = false
        for manager in managers { manager.stopAdvertising(); manager.removeAllServices(); manager.delegate = nil }
        managers.removeAll(); queues = [[], []]; values = [[:], [:]]
    }
    override func viewDidDisappear(_ animated: Bool) { stop(); super.viewDidDisappear(animated) }
    private func record(_ message: String) { output.text += "\(Date()) \(message)\n" }
    private func start(disjoint: Bool) {
        stop()
        guard let url = Bundle.main.url(forResource: "catalog", withExtension: "json", subdirectory: "shared/generated"),
              let data = try? Data(contentsOf: url), let catalog = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]],
              let first = catalog.first(where: { $0["board"] as? String == "kilter" }),
              let second = catalog.first(where: { $0["board"] as? String == (disjoint ? "quantum" : "tension") }) else { record("Missing catalog"); return }
        profiles = [first, second]; active = true
        for _ in 0..<2 { managers.append(CBPeripheralManager(delegate: self, queue: .main)) }
    }
    func peripheralManagerDidUpdateState(_ manager: CBPeripheralManager) {
        guard active, let slot = managers.firstIndex(where: { $0 === manager }) else { return }
        record("manager=\(slot) state=\(manager.state.rawValue)")
        guard manager.state == .poweredOn else { return }
        for spec in profiles[slot]["services"] as? [[String: Any]] ?? [] {
            let service = CBMutableService(type: CBUUID(string: spec["uuid"] as! String), primary: true)
            var attributes: [CBMutableCharacteristic] = []
            for entry in spec["characteristics"] as? [[String: Any]] ?? [] {
                let id = CBUUID(string: entry["uuid"] as! String), flags = entry["flags"] as! [String]
                var properties: CBCharacteristicProperties = [], permissions: CBAttributePermissions = []
                if flags.contains("read") { properties.insert(.read); permissions.insert(.readable) }
                if flags.contains("write") { properties.insert(.write); permissions.insert(.writeable) }
                if flags.contains("write-without-response") { properties.insert(.writeWithoutResponse); permissions.insert(.writeable) }
                if flags.contains("notify") { properties.insert(.notify) }
                attributes.append(CBMutableCharacteristic(type: id, properties: properties, value: nil, permissions: permissions))
                values[slot][id] = Data(entry["value"] as? [UInt8] ?? [])
            }
            service.characteristics = attributes; queues[slot].append(service)
        }
        publish(slot)
    }
    private func publish(_ slot: Int) {
        if !queues[slot].isEmpty { managers[slot].add(queues[slot].removeFirst()); return }
        managers[slot].startAdvertising([
            CBAdvertisementDataLocalNameKey: profiles[slot]["name"] as! String,
            CBAdvertisementDataServiceUUIDsKey: [CBUUID(string: profiles[slot]["advertised"] as! String)]])
    }
    func peripheralManager(_ manager: CBPeripheralManager, didAdd service: CBService, error: Error?) {
        guard active, let slot = managers.firstIndex(where: { $0 === manager }) else { return }
        record("manager=\(slot) service=\(service.uuid) error=\(String(describing: error))")
        if error == nil { publish(slot) }
    }
    func peripheralManagerDidStartAdvertising(_ manager: CBPeripheralManager, error: Error?) {
        guard let slot = managers.firstIndex(where: { $0 === manager }) else { return }
        record("manager=\(slot) advertising error=\(String(describing: error)); independent identities UNPROVEN")
    }
    func peripheralManager(_ manager: CBPeripheralManager, didReceiveWrite requests: [CBATTRequest]) {
        guard let slot = managers.firstIndex(where: { $0 === manager }), let first = requests.first else { return }
        for request in requests { record("WRITE manager=\(slot) peer=\(request.central.identifier) uuid=\(request.characteristic.uuid) offset=\(request.offset) data=\(request.value?.map { String(format: "%02x", $0) }.joined() ?? "")") }
        manager.respond(to: first, withResult: requests.contains(where: { $0.offset != 0 }) ? .invalidOffset : .success)
    }
    func peripheralManager(_ manager: CBPeripheralManager, didReceiveRead request: CBATTRequest) {
        guard let slot = managers.firstIndex(where: { $0 === manager }) else { return }
        record("READ manager=\(slot) peer=\(request.central.identifier) uuid=\(request.characteristic.uuid)")
        let data = values[slot][request.characteristic.uuid] ?? Data()
        guard request.offset <= data.count else { manager.respond(to: request, withResult: .invalidOffset); return }
        request.value = data.subdata(in: request.offset..<data.count); manager.respond(to: request, withResult: .success)
    }
}
