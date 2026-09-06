# Linux/mobile feature parity audit

Full parity is **not achieved**. The original one-board mobile release is an
intermediate result, not an accepted reduction of the objective. This audit
compares the current Linux implementation (including `ble/multiplex.py`) with
mobile code and distinguishes implementation from physical validation.

| Feature | Current Linux | Android | iOS |
|---|---|---|---|
| Eight boards, all layouts/sizes | Registry, 52 configurations | Same generated registry | Same generated registry |
| Aurora API 2/3, board-local colors | Python decoder and role resolver | Shared conformance-tested decoder/palettes | Identical shared decoder/palettes |
| MoonBoard, five variants | ASCII, split frames, clear, aux semantics | Same tested semantics/maps | Same tested semantics/maps |
| Quantum XL/L/M/S/Belay | Binary 2.0.14/1.44, JSON, layers, roster | Same tested semantics, reads/targeted notifications | Same tested semantics, reads/targeted notifications |
| Accurate images/coordinates | DBs, maps, calibrated Quantum transform | Byte-identical assets, exported transforms | Byte-identical assets, exported transforms |
| Resizing/display | Tk, headless; multi panels | Responsive canvas per panel | Responsive canvas per panel |
| Runtime board/layout/size/API change | Rebuild session/peripheral | Rebuild selected endpoint; generation guards drop old writes | Rebuild selected endpoint; generation guards drop old writes |
| Two independent rendered states | Two sessions | Implemented in two panels | Implemented in two panels |
| Two real BLE endpoints, disjoint services | Supported on capable controller | **Experimental implemented**: two GATT servers + advertising sets; Quantum + Aurora/Moon only | **Experimental implemented**: two peripheral managers; Quantum + Aurora/Moon only |
| Separate scanner identities in two-board mode | Static-random local addresses and names | Name snapshots serialized; address/discovery/routing tests **pending** | OS controls advertising/name placement; independent scanner identities **unproven** |
| Two Aurora, two Moon, Aurora+Moon | HCI-to-advertiser routing | **Unfulfilled**; duplicate/overlapping profiles rejected | **Unfulfilled**; duplicate/overlapping profiles rejected |
| Two Quantum | Isolated reads; shared-UUID notify suppressed | **Unfulfilled**; duplicate profiles rejected | **Unfulfilled**; duplicate profiles rejected |
| Exclusive, one board | Stops advertising on connection; resumes on disconnect | Implemented with real connection callbacks and retained advertising set | **Partial**: stop at first ATT access; no generic peripheral disconnect callback; manual resume required |
| Exclusive, two disjoint boards | Per-advertiser connection assignment | **Partial/experimental**: assignment at that server's GATT access, not connection event | **Partial/experimental**: assignment at ATT access, manual resume |
| Multi-connect | Advertising stays enabled; baseline has one decoder per board | Separate mode; per-controller streaming buffers, shared board state | Separate mode; per-central streaming buffers, shared board state |
| Independent fragment streams | Baseline callback lacks peer argument to session | Interleaving/reset/roster tests pass | Same shared tests pass |
| Foreground lifecycle | Persistent root / explicit shutdown | Background stops endpoints | Background stops managers |
| Installability | Existing Linux runtime | Debug APK builds; updated version device tests **pending** | Unsigned builds; physical install **blocked by signing** |
| Official app matrix | Existing manual workflow | Full named-app matrix **pending** | **Pending**, including discovery limits |

Native mode changes, concurrent endpoints, scanner identities, notifications to
multiple physical controllers and exclusive reconnect behavior are not claimed
as hardware-tested. A previous single-board build received five real Kilter
climbs on Android 15. That does not validate the new modes or identify the
controller application. Android 9 remains physically untested.

## Why the Linux routing mechanism matters

`ble/multiplex.py` merges GATT profiles but does not guess the board from UUIDs.
It configures two static-random addresses and uses `ble/hci_monitor.py` to observe
HCI Advertising Set Terminated: advertising handle + connection handle + peer.
The resulting peer-to-slot assignment routes writes and reads, even when both
boards use the same UART UUID. This evidence is absent from public mobile GATT
callbacks. Two active advertisements by themselves prove neither independent
addressability nor correct GATT routing.

Android's `BluetoothGattServerCallback` identifies remote device and accessed
characteristic; `AdvertisingSetCallback` identifies the advertising set but not
the accepted peer/connection handle. Inspection with `javap` of installed API 35
and API 36 public SDK jars confirmed that neither exports an advertiser/GATT
server binding or the required HCI connection callback. API 28-era AOSP source
provides the lower-level evidence: in
[`android-9.0.0_r1/stack/btm/btm_ble_multi_adv.cc`](https://android.googlesource.com/platform/system/bt/+/android-9.0.0_r1/stack/btm/btm_ble_multi_adv.cc),
`OnAdvertisingSetTerminated` receives the handles internally, updates the
connection address and re-enables the set; the application does not receive the
association. This also explains why the initial app's advertising could continue
after connection. See the public
[GATT callbacks](https://developer.android.com/reference/android/bluetooth/BluetoothGattServerCallback)
and [advertising callbacks](https://developer.android.com/reference/android/bluetooth/le/AdvertisingSetCallback).
Newer AOSP internals mentioning isolated servers are not evidence of an API
available to an ordinary APK on stock Android 9/15; no hidden/privileged API is
used or promised here.

CoreBluetooth's
[peripheral delegate](https://developer.apple.com/documentation/corebluetooth/cbperipheralmanagerdelegate)
reports ATT requests and subscriptions, not generic link connect/disconnect or
local advertising addresses. Central-manager disconnect callbacks concern a
phone acting as a **central**, not the requested peripheral role. Subscribing or
unsubscribing is not proof of connection establishment/termination. The iOS
exclusive implementation therefore documents and exposes a manual resume after
the controller disconnects instead of inventing a disconnect event.

## Implemented disjoint-service experiment

Choose two instances, select Quantum on one and any Aurora/MoonBoard on the
other, then Start each. Each panel owns its own state, central decoder map,
GATT endpoint, lifecycle, reads and notification queue. Writes are routed by the
actual native characteristic/server callback to the corresponding panel; data
is never copied to both panels or classified using arbitrary continuation bytes.
Duplicate service UUIDs are rejected before registering an ambiguous second
endpoint. Android serializes initial adapter renames and advertiser registration;
exclusive pause/resume toggles the existing set to retain its original name.
iOS uses two managers but cannot guarantee two independent names/addresses.

This mode performs real native BLE operations, not UI playback. It remains
experimental until external scan/connect/write tests prove the actual phone's
behavior. A controller connected through a shared OS GATT database may discover
both service families; disjoint characteristics make data routing explicit, but
do not establish Linux-equivalent advertising-identity isolation. Disconnecting
an OS-shared link can affect another endpoint accessed through that same link.

## Reproducible routing probes and pending physical work

Both apps include **Developer diagnostics → Stop board and open two-advertiser
routing experiment**. No probe starts until its Start button is pressed.
It publishes either two UART profiles (Kilter/Tension) or disjoint profiles
(Kilter/Quantum), records both advertisement callbacks, and logs each remote
peer, accessed characteristic and receiving server/manager. It does not decode
or claim full simulation; it exists to falsify/confirm routing assumptions.

For each pair, on a separate central:

1. Scan and record both local names, addresses (Android central) or identifiers
   (iOS central), service UUIDs and complete/truncated names.
2. Connect to each advertised identity in turn; enumerate the complete GATT DB,
   including duplicate UUIDs and characteristic instance/handle information.
3. Write a distinct known packet to each chosen board's normal characteristic.
   Compare the receiving server/manager log with the selected advertisement.
4. Repeat with two simultaneous controllers and interleaved fragments; then
   reconnect and reverse connection order. No inference from timing is accepted.
5. Test the actual two-panel disjoint mode with independent clears/Quantum state,
   exclusive and multi-connect separately. Verify which advertisement disappears
   on first connection versus first ATT activity and which resumes after loss.

**Physical probes are not executed after the user's ADB ban.** No device access
(read-only included) is allowed until explicit release. iOS physical tests also
need signing and an accessible iPhone. SDK inspection, compilation, simulator UI
and pure protocol tests are evidence for different claims, not substitutes.

## Concrete alternatives requiring a user decision

If physical tests confirm the public-API boundary, full duplicate-family parity
on the specified phones remains unfulfilled. Options are:

- Keep the native disjoint mode and implement explicit controller-to-board
  binding on one shared GATT endpoint for duplicate families. That can preserve
  real BLE and separate state, but changes discovery/connection semantics and
  requires manual assignment; it is **not automatically accepted as parity**.
- Use an explicitly provisioned privileged Android/LineageOS service exposing
  HCI/local-advertiser routing. This changes the stock Android requirement and
  has no equivalent ordinary iOS API; it is **not deployed here**.
- Use external independent BLE controllers under the mobile app's control, or
  Linux as the radio host. This changes the “boards on the phone's own radio”
  requirement; no hardware purchase or alternate architecture is assumed.

No option above is silently selected. The present deliverable remains partial
until the missing routing/identity/lifecycle behavior is verified and implemented
or the user explicitly accepts a changed requirement.
