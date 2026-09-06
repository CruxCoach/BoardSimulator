# Mobile architecture and platform limits

Assessment date: 2026-09-06. These apps are foreground BLE **peripherals**;
CruxCoach or the official app runs on a different physical device. No network
service, Web Bluetooth, mocked radio, pairing workflow or app store is required.
Android 15 advertising and reception from an unidentified external controller
have been observed; the named-app interoperability matrix remains unverified.

## Decision

Keep the working Python/BlueZ/Tk Linux implementation. Android uses Java with
`BluetoothGattServer` and `BluetoothLeAdvertiser`, iOS uses Swift with
`CBPeripheralManager`. Both embed the same offline HTML canvas UI and JavaScript
streaming decoders in their platform WebView. The WebView is a local renderer and
protocol engine, not the BLE transport. There are no npm runtime dependencies.

`tools/export_mobile.py` derives all 52 board/layout/size configurations from
`boards.py`, including eight boards and all five Quantum models. It exports the
actual session GATT profiles, initial Quantum identity record, normalized
physical LED coordinates and board-local role palettes for both Aurora API
levels. Original images and MoonBoard maps are copied byte-for-byte. Quantum
coordinates use the same calibration as the Linux square board view.
Missing Aurora photographs use accurate hold dots, as on Linux.

This avoids rewriting Linux, duplicating SQLite queries in Java/Swift, or
introducing Flutter/React Native and a third-party BLE plugin whose peripheral
support might be incomplete. The tradeoff is a small JavaScript port of the
three Python decoders. Python-generated conformance traces exercise that port
on Node; both apps ship those exact scripts. Native GATT/lifecycle paths still
need physical tests. JavaScript and native callbacks are serialized on each
platform's main thread. Native reads evaluate the shared state after queued
writes, avoiding a stale native copy of Quantum fff4.

## Android 9 through Android 15

Minimum SDK 28, target/compile SDK 35, Java 17 build toolchain. GATT server and
legacy advertising APIs predate API 28. Runtime checks require Bluetooth on,
an advertiser and `isMultipleAdvertisementSupported()`. Failure is shown, not
replaced with a simulated connection. Android 12+ requests `BLUETOOTH_ADVERTISE`
and `BLUETOOTH_CONNECT`; older versions use manifest Bluetooth permissions.
There is no scan, so no location or scan permission is requested. See Google's
[Bluetooth permissions](https://developer.android.com/develop/connectivity/bluetooth/bt-permissions)
and [BluetoothAdapter API](https://developer.android.com/reference/android/bluetooth/BluetoothAdapter).

One full service UUID is advertised; the complete board name is in the scan
response. Advertising starts only after all GATT services register. Android's
local name belongs to the adapter: the app temporarily renames it and restores
the previous name on Stop/background if it still owns that name. An abrupt
process kill can prevent restoration. Verify the complete `#0001@2/3` suffix in
an active scan; cached device names can differ from scan-response names.
See [BluetoothLeAdvertiser](https://developer.android.com/reference/android/bluetooth/le/BluetoothLeAdvertiser).

The Android server accepts one controller and disconnects additional clients to
avoid interleaving partially received climbs. Disconnect drops transport buffers
but retains the displayed/controller state. Going to the background stops BLE;
Start creates a new empty session. Rotation keeps the current Activity/session.
Prepared writes and nonzero write offsets are rejected explicitly. Reads honor
ATT offsets; notifications wait for `onNotificationSent`.

## Why one board per phone

Android advertising sets are not separate physical controllers. The GATT server
callback exposes a **remote device and characteristic**, not an advertising-set
identity or the local address a client selected. Starting duplicate UART
services with multiple advertising sets therefore does not give a documented,
reliable way to route writes to independent Kilter/Tension/MoonBoard instances.
This is an engineering inference from the public
[GATT callback contract](https://developer.android.com/reference/android/bluetooth/BluetoothGattServerCallback)
and [advertising API](https://source.android.com/docs/core/connect/bluetooth/ble_advertising),
not a claim that a particular chipset cannot emit multiple advertisements.

CoreBluetooth publishes to the local GATT database and exposes no public control
over multiple independent peripheral addresses or HCI advertising sets. Multiple
managers are not a supported way to promise separate virtual boards. See
[CBPeripheralManager](https://developer.apple.com/documentation/corebluetooth/cbperipheralmanager).
Both mobile UIs therefore explicitly offer one board. Linux retains its existing
multi-adapter and virtual-instance modes; use those or additional phones for
independent simultaneous boards. Multiple controllers connected to one board
are a different feature from independently addressable boards.

## iOS specifics

Deployment target iOS 15. Bluetooth authorization is requested only on Start.
Denied, unsupported, powered-off and registration/advertising errors are visible.
No background mode is declared; backgrounding removes services and advertisements.
CoreBluetooth controls packet placement and advertises on a best-effort basis.
Only local name and service UUIDs can be supplied; there is no raw advertising
packet API. Apple documents 28 bytes for foreground advertisement values plus
10 bytes for a local name in scan response (per-type headers are additional).
A long Aurora name and a 128-bit discovery UUID can therefore compete for space.
If the API suffix is absent in the observed scan, try the explicit Aurora API 2
selection, since controllers may default to that protocol level. This is a
diagnostic workaround, not evidence that a particular official scanner works. In the background the local name disappears and UUID handling changes.
Consequently official-app discovery must be checked on the actual iPhone, with
particular attention to long Aurora names and their API-level suffix. See
[Apple's peripheral guide](https://developer.apple.com/library/archive/documentation/NetworkingInternetWeb/Conceptual/CoreBluetooth_concepts/PerformingCommonPeripheralRoleTasks/PerformingCommonPeripheralRoleTasks.html)
and [startAdvertising](https://developer.apple.com/documentation/corebluetooth/cbperipheralmanager/startadvertising(_:)).

CoreBluetooth has subscription callbacks, but no generic disconnect callback for
a UART writer that never subscribes. An iOS run therefore locks writes to its
first central identifier; Stop/Start releases that ownership and resets partial
transport state. After an interrupted Aurora/MoonBoard transfer, Stop/Start
before reconnecting. Quantum unsubscribe resets transport and preserves state.
Do not interpret a successful write as proof that a central remains connected.

Quantum fff1 notifications use `updateValue` backpressure and resume on
`peripheralManagerIsReady(toUpdateSubscribers:)`. We do not invent fragmentation
for a broadcast larger than the central's negotiated notification limit; the UI
reports it and fff4 remains readable. Android likewise checks MTU minus three.
The first active-route broadcast is 41 bytes and needs ATT MTU at least 44.
More simultaneous Quantum routes need larger negotiated MTUs. See
[updateValue](https://developer.apple.com/documentation/corebluetooth/cbperipheralmanager/updatevalue(_:for:onsubscribedcentrals:)).

Quantum notifications/state reproduce the repository's eWalls 2.0.14 compatibility
model; controller firmware values/timing are not hardware-captured. Current and
legacy binary commands and legacy JSON are decoded. MoonBoard Mini serpentine
wiring retains the Linux assumption pending capture. Duration/animation data is
decoded; automatic expiry and animation are not modeled by the Linux baseline or
mobile implementation. Same-device controller operation is outside this work.
