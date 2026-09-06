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
    private var peripheral: Peripheral!
    override func viewDidLoad() {
        super.viewDidLoad()
        let configuration = WKWebViewConfiguration()
        configuration.userContentController.add(self, name: "ble")
        web = WKWebView(frame: .zero, configuration: configuration)
        web.navigationDelegate = self
        view = web
        peripheral = Peripheral(controller: self)
        guard let url = Bundle.main.url(forResource: "index", withExtension: "html", subdirectory: "shared") else {
            web.loadHTMLString("Missing bundled resources. Run tools/export_mobile.py before building.", baseURL: nil)
            return
        }
        web.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
        NotificationCenter.default.addObserver(self, selector: #selector(background), name: UIApplication.didEnterBackgroundNotification, object: nil)
        UIApplication.shared.isIdleTimerDisabled = true
    }
    @objc private func background() {
        peripheral.stop()
        status("Stopped in background. Press Start to resume.", running: false)
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        decisionHandler(navigationAction.request.url?.isFileURL == true ? .allow : .cancel)
    }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, let text = message.body as? String,
              let data = text.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        switch object["command"] as? String {
        case "start":
            if let profile = object["profile"] as? [String: Any] { peripheral.start(profile) }
        case "stop": peripheral.stop(); status("Stopped", running: false)
        case "ready": status("iOS · One BLE identity · Start to check Bluetooth availability", running: false)
        case "copy": UIPasteboard.general.string = object["text"] as? String
        default: status("Unknown bridge command", running: false)
        }
    }
    func status(_ text: String, running: Bool) {
        let json = String(data: try! JSONSerialization.data(withJSONObject: [text, running]), encoding: .utf8)!
        web.evaluateJavaScript("BLE.status.apply(null,\(json))", completionHandler: nil)
    }
    func receive(_ value: Data, completion: @escaping ([[String: Any]]) -> Void) {
        web.evaluateJavaScript("BLE.receive(\(Array(value)))") { result, error in
            if let error = error { self.peripheral.stop(); self.status("Decoder error: \(error.localizedDescription)", running: false) }
            completion(result as? [[String: Any]] ?? [])
        }
    }
    func readState(_ completion: @escaping (Data) -> Void) {
        web.evaluateJavaScript("BLE.readState()") { result, error in
            if let bytes = result as? [UInt8] { completion(Data(bytes)) }
            else { self.peripheral.stop(); self.status("State read failed: \(String(describing: error))", running: false) }
        }
    }
    func disconnected() { web.evaluateJavaScript("BLE.disconnected()", completionHandler: nil) }
}
