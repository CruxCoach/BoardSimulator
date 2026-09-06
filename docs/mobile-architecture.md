# Mobile architecture and platform constraints

The objective is feature parity with the current Linux simulator, including
multiple real boards in one app. **Full parity is not achieved.** The authoritative
[feature matrix and routing assessment](mobile-parity.md) lists implemented,
experimental, unfulfilled and physically untested behavior separately.

## Shared and native responsibilities

Linux retains Python/BlueZ/Tk and its existing multi-adapter/multiplex modes.
Android uses Java `BluetoothGattServer` and `BluetoothLeAdvertiser`; iOS uses
Swift `CBPeripheralManager`. Both embed identical offline JavaScript streaming
decoders and canvas panels. WebViews render and decode; native APIs provide the
actual peripheral radio. There is no Web Bluetooth, remote web service or mock
radio in either application. Browser UI tests explicitly mock the bridge.

`tools/export_mobile.py` exports all 52 board/layout/size selections from the
Linux registry, session GATT profiles/initial values, normalized physical LED
coordinates and board-local palettes for Aurora APIs 2 and 3. Original assets,
including MoonBoard JSON maps, are copied byte-for-byte. Quantum uses precisely
the existing Linux renderer's calibration. Aurora sizes without photographs
retain accurate hold dots.

A `BoardSession` owns one board's state. Each controller has its own streaming
decoder, CRC/ASCII buffers and Quantum continuation marker. Completed Aurora/Moon
messages replace that board's displayed state. Quantum route/editor layers and
roster are shared across controllers of the same board, but never another board.
State reads are evaluated through the WebView after earlier queued writes.
Runtime selection changes rebuild one endpoint; monotonic run tokens reject
writes queued for an obsolete board selection. Panel disposal removes resize
listeners and releases old images.

Two panels can host two native endpoints when their GATT service sets are
disjoint: Quantum plus Aurora or MoonBoard. The accessed native service/server
routes bytes to its own panel. Overlapping service UUIDs are rejected. Android
serializes initial name changes/advertiser setup and retains advertising sets
across exclusive pause/resume. iOS runs separate peripheral managers, but name
and address isolation remains under OS control. This is an **experimental**
partial implementation requiring external scanner/controller validation; it is
not Linux-equivalent independent identity routing for arbitrary board pairs.

## Android

Minimum API 28, target/compile API 35, Java 17 toolchain. GATT server and
`startAdvertisingSet` with legacy connectable/scannable parameters are available
before API 28. Startup checks Bluetooth state, advertiser availability and
multiple-advertisement capability, then reports service/set registration errors.
A second set may be rejected by the actual controller; the UI exposes that error.

Android 12+ requests `BLUETOOTH_ADVERTISE` and `BLUETOOTH_CONNECT`. Older versions
use manifest Bluetooth permissions. The app does not scan, so it requests no
location/scan permission. See Google's
[permission contract](https://developer.android.com/develop/connectivity/bluetooth/bt-permissions).
The full service UUID is in advertising data and the board name in scan response.
Android's name is adapter-global, so initial endpoint starts are serialized and
the prior name is restored on normal shutdown if it still belongs to this app.
An abrupt kill can prevent restoration. See
[advertising APIs](https://developer.android.com/reference/android/bluetooth/le/BluetoothLeAdvertiser).

Single-board Exclusive stops the existing advertising set on the connection
callback and resumes it after the final disconnect. Multi-connect keeps it
enabled and permits separate controller streams. In two-endpoint mode, a
connection callback lacks the accepted advertising identity; board ownership is
only established by that server's ATT access. Exclusive behavior is therefore
partial in that mode. Unassigned links belonging to another endpoint are not
cancelled during a slot teardown; an OS-shared link used by both endpoints may
still affect both on disconnect. Prepared writes and nonzero write offsets are
rejected; reads honor offsets. Notifications are queued per target/MTU and wait
for the native sent callback.

## iOS

Deployment target iOS 15. Bluetooth authorization is prompted on Start. Denied,
unsupported, powered-off and registration/advertising errors are visible. No
background mode is declared; backgrounding removes advertisements and services.

CoreBluetooth permits local name and service UUIDs only, with best-effort packet
placement. Apple documents 28 bytes for foreground advertisement values plus
10 bytes of scan-response local-name space, with additional per-type headers.
A long Aurora name and a 128-bit discovery UUID compete for space; the API suffix
may disappear. Observe the actual scan and, if a controller defaults to API 2,
try that explicit selection. This is a diagnostic step, not proof of official-app
compatibility. See
[startAdvertising](https://developer.apple.com/documentation/corebluetooth/cbperipheralmanager/startadvertising(_:)).

There is no generic peripheral connect/disconnect callback for a UART writer.
Exclusive stops at first observable ATT read/write/subscription and retains
ownership until the user disconnects the controller and presses Resume
advertising. Unsubscribe is not treated as proof of disconnection. Multi-connect
accepts separate central streams. Strict Linux-equivalent automatic exclusive
lifecycle remains unfulfilled; see the
[peripheral delegate](https://developer.apple.com/documentation/corebluetooth/cbperipheralmanagerdelegate).

Quantum notifications use `updateValue` backpressure and resume on the ready
callback. Neither platform invents fragmentation for a broadcast larger than
its central's notification size: diagnostics explain the limit and fff4 remains
readable. A one-route broadcast is 41 bytes (ATT MTU at least 44); more routes
need more space. Exceptions are targeted to the writing subscriber, while
board-wide updates reach subscribers of that board. See
[updateValue](https://developer.apple.com/documentation/corebluetooth/cbperipheralmanager/updatevalue(_:for:onsubscribedcentrals:)).

## Evidence and remaining acceptance gates

The tradeoff of keeping Linux intact is a small JavaScript protocol port, checked
against Python-generated reference traces, interleaved-controller invariants and
browser panel integration. Native Android builds/lint and macOS iOS compilation,
resource checks and simulator launch are separate gates. They do not prove BLE.
A previous single-board APK received real climbs on Android 15; the new modes
remain physically untested under the user's device-access ban.

Quantum response timing/firmware identity and MoonBoard Mini wiring retain the
Linux baseline's unverified hardware assumptions. Automatic duration expiry and
animation are not simulated by the baseline or mobile code. iOS physical
installation requires signing material and device provisioning; unsigned CI ZIPs
are not installable IPAs. Same-device controller operation is not a substitute
for the required real peripheral and remains outside this implementation.
