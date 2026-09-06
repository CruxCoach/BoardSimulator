import UIKit
import WebKit
import CoreBluetooth

@main
final class AppDelegate: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    func application(_ application: UIApplication, didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        let window = UIWindow(frame: UIScreen.main.bounds)
        window.rootViewController = BoardController()
        window.makeKeyAndVisible()
        self.window = window
        return true
    }
}

/// An offline WKWebView shares tested protocol code with Android; CoreBluetooth
/// owns the peripheral. All callbacks execute on the main queue.
final class BoardController: UIViewController, WKScriptMessageHandler, WKNavigationDelegate {
    private var web: WKWebView!
    private var peripherals: [Peripheral] = []
    private var profiles: [[String: Any]?] = [nil, nil]
    override func viewDidLoad() {
        super.viewDidLoad()
        let configuration = WKWebViewConfiguration()
        configuration.userContentController.add(self, name: "ble")
        web = WKWebView(frame: .zero, configuration: configuration)
        web.navigationDelegate = self
        view = web
        peripherals = (0..<2).map { Peripheral(controller: self, slot: $0) }
        guard let url = Bundle.main.url(forResource: "index", withExtension: "html", subdirectory: "shared") else {
            web.loadHTMLString("Missing bundled resources. Run tools/export_mobile.py before building.", baseURL: nil)
            return
        }
        web.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
        NotificationCenter.default.addObserver(self, selector: #selector(background), name: UIApplication.didEnterBackgroundNotification, object: nil)
        UIApplication.shared.isIdleTimerDisabled = true
    }
    @objc private func background() {
        for slot in 0..<2 { peripherals[slot].stop();status("Stopped in background. Press Start to resume.", running: false, slot: slot) }
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        decisionHandler(navigationAction.request.url?.isFileURL == true ? .allow : .cancel)
    }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, let text = message.body as? String,
              let data = text.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        let slot = object["slot"] as? Int ?? 0
        guard (0..<2).contains(slot) else { return }
        switch object["command"] as? String {
        case "start":
            if let profile = object["profile"] as? [String: Any] {
                peripherals[slot].stop(); profiles[slot] = nil
                let ids = Set((profile["services"] as? [[String: Any]] ?? []).compactMap { $0["uuid"] as? String })
                let other = Set((profiles[1-slot]?["services"] as? [[String: Any]] ?? []).compactMap { $0["uuid"] as? String })
                if !ids.isDisjoint(with: other) { status("Overlapping GATT profiles require unavailable advertising-to-link routing. Full parity is pending.", running: false, slot: slot); return }
                profiles[slot] = profile
                peripherals[slot].start(profile, multi: object["multi"] as? Bool ?? false)
            }
        case "instances": background()
        case "probe": background(); present(RoutingProbeController(), animated: true)
        case "connections": peripherals[slot].setMulti(object["multi"] as? Bool ?? false)
        case "release": peripherals[slot].releaseController()
        case "stop": peripherals[slot].stop(); status("Stopped", running: false, slot: slot)
        case "ready": web.evaluateJavaScript("BLE.canResume();BLE.canProbe()", completionHandler: nil); status("iOS · Start to check Bluetooth availability", running: false, slot: slot)
        case "copy": UIPasteboard.general.string = object["text"] as? String
        default: status("Unknown bridge command", running: false, slot: slot)
        }
    }
    func status(_ text: String, running: Bool, slot: Int = 0) {
        if !running { profiles[slot] = nil }
        let json = String(data: try! JSONSerialization.data(withJSONObject: [text, running, slot]), encoding: .utf8)!
        web.evaluateJavaScript("BLE.status.apply(null,\(json))", completionHandler: nil)
    }
    func receive(_ value: Data, peer: UUID, token: Int, slot: Int, completion: @escaping ([[String: Any]]) -> Void) {
        web.evaluateJavaScript("BLE.receive(\(Array(value)), \"\(peer.uuidString)\", \(token), \(slot))") { result, error in
            if let error = error { self.peripherals[slot].stop(); self.status("Decoder error: \(error.localizedDescription)", running: false, slot: slot) }
            completion(result as? [[String: Any]] ?? [])
        }
    }
    func readState(slot: Int, _ completion: @escaping (Data) -> Void) {
        web.evaluateJavaScript("BLE.readState(\(slot))") { result, error in
            if let bytes = result as? [UInt8] { completion(Data(bytes)) }
            else { self.peripherals[slot].stop(); self.status("State read failed: \(String(describing: error))", running: false, slot: slot) }
        }
    }
    func disconnected(_ peer: UUID, slot: Int) { web.evaluateJavaScript("BLE.disconnected(\"\(peer.uuidString)\", \(slot))", completionHandler: nil) }
}
