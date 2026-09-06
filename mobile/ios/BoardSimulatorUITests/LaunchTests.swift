import XCTest

final class LaunchTests: XCTestCase {
    func testOfflineInterfaceLoadsWithNativeBridge() {
        let app = XCUIApplication()
        app.launch()
        // This status is posted by native code only after app.js has loaded,
        // built the catalog selectors, and called the trusted bridge's ready.
        let ready = app.webViews.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "Start to check Bluetooth availability")).firstMatch
        XCTAssertTrue(ready.waitForExistence(timeout: 30), "Bundled JavaScript and CoreBluetooth bridge must initialize")
        XCTAssertTrue(app.webViews.buttons["Start BLE"].exists)
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
