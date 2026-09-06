# Building, installing and validating mobile apps

**Current status:** full parity is unfulfilled; see [mobile-parity.md](mobile-parity.md).
The new multi-connect, exclusive and two-endpoint modes require physical tests.
Do not access the attached ADB device until the user explicitly lifts the ban.

## Android debug build

Install Python 3.10+, requirements.txt, JDK 17 and Android SDK platform 35.
From the repository root:

```sh
python tools/export_mobile.py
cd mobile/android
./gradlew assembleDebug lintDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Set `ANDROID_HOME` to your SDK or use an untracked `mobile/android/local.properties`.
The Gradle wrapper pins 8.13, Android Gradle Plugin is 8.13.2. The APK is debug
signed and minSdk 28, suitable for direct testing on Android 9 and Android 15.
No device is required to compile. Grant Nearby devices permission when prompted
on Android 12+, enable Bluetooth, select a configuration, then Start BLE. Keep
the app in front. Enable installation from the chosen file provider if installing
by opening the APK instead of adb. CI publishes `boardsimulator-android-debug`.
Debug certificates differ across build machines; if updating fails with a
certificate mismatch, uninstall that previous debug build before reinstalling.

## iOS without a local Mac

The GitHub Actions `ios` job uses macOS and Xcode to generate a project with
XcodeGen, compile an **unsigned physical-iPhone app**, and compile a simulator
app. The `boardsimulator-ios-unsigned` artifact contains two ZIPs. Neither the
unsigned device ZIP nor the simulator app is an installable signed iPhone build.
A green build verifies compilation and bundling, not Bluetooth or signing.

On a Mac (local or an existing macOS build runner):

```sh
python tools/export_mobile.py
brew install xcodegen
cd mobile/ios
xcodegen generate
xcodebuild -project BoardSimulator.xcodeproj -scheme BoardSimulator \
  -configuration Debug -sdk iphoneos -destination 'generic/platform=iOS' \
  -derivedDataPath build/device CODE_SIGNING_ALLOWED=NO build
```

For installation, choose one existing signing path:

1. **Existing Apple Developer Program team**: register the iPhone UDID and bundle
   identifier, create a development or ad-hoc provisioning profile and matching
   signing certificate. Supply the certificate/private key and profile to a
   trusted macOS runner using encrypted CI secrets; never commit them. Archive
   with that team and export a signed development/ad-hoc IPA using an
   `ExportOptions.plist` for the matching profile. Install through an existing
   managed/ad-hoc device deployment path or Xcode on an accessible Mac. See
   [registered-device distribution](https://developer.apple.com/documentation/xcode/distributing-your-app-to-registered-devices).
2. **Free Personal Team**: Apple's supported path requires signing into Xcode
   with an Apple Account and provisioning the actual device. Profiles expire
   after seven days. A hosted unsigned CI build alone does not provide this
   account/device workflow. With no accessible Mac and no existing signing
   assets, this path remains blocked; no paid enrollment or third-party signing
   service is provisioned here. See
   [Apple developer account overview](https://developer.apple.com/help/account/basics/about-your-developer-account).

For an existing provisioned team, the archive/export commands are:

```sh
xcodebuild -project BoardSimulator.xcodeproj -scheme BoardSimulator \
  -destination 'generic/platform=iOS' -archivePath build/BoardSimulator.xcarchive \
  DEVELOPMENT_TEAM=YOUR_TEAM_ID archive
xcodebuild -exportArchive -archivePath build/BoardSimulator.xcarchive \
  -exportPath build/signed -exportOptionsPlist ExportOptions.plist
```

These commands need installed matching signing material and a profile containing
the device. They have not been run with credentials. Developer Mode may need to
be enabled on the iPhone. Store release/TestFlight and paid service provisioning
are outside scope. Do not upload Apple account passwords or private keys in issues.

## Automated protocol checks

```sh
python -m pytest tests/ -q
python tools/export_mobile.py
python tools/mobile_fixtures.py
node mobile/tests/conformance.cjs
node mobile/tests/controllers.cjs
```

94 reference traces cover all 52 selections, Aurora APIs 2/3, board-local palettes,
multipart messages, bad checksums, clear, MoonBoard auxiliary LED semantics,
Quantum 2.0.14 and 1.44 commands, CRC failures, multi-chunk routes, multiple users,
route removal/swipe, reconnect, editor layers, JSON and state/notify payloads.
376 replays vary write fragmentation (1, 7, 20, 512 bytes). This is independent
Python-vs-JavaScript conformance, not proof of official-app interoperability.

## Physical validation matrix

A Nokia 6.1 running Android 15/API 35 was available over ADB during implementation.
The earlier single-board debug APK installed and cold-launched successfully; this is historical evidence, not validation of the current parallel/multi-connect implementation. The normal Nearby devices
permission flow, Bluetooth-off error and successful Kilter advertising callback
were observed. An external central then sent five complete Aurora API 3 climbs
fragmented across 20-byte writes: 10, 12, 8, 12 and 9 holds. The live view reported
9 holds for the final climb; replaying the captured RX bytes through the Linux
reference decoder produced those same five counts. The controller app/version
was not identified, so this is a real BLE reception check, not a claim about
CruxCoach or a specific official app. Other boards and the full matrix remain
pending.

All named app/configuration cells below are **not tested** until identified tests
are recorded. Controller and
simulator ordinarily run on separate devices. Record app versions, exact phone
model/OS, selected board/layout/size/API, advertisement name/UUID, negotiated MTU,
GATT discovery, raw RX diagnostics, screenshot and result for each run.

| Peripheral | Controller | Boards/configurations | Status |
|---|---|---|---|
| Android 9 stock | CruxCoach on another device | All 52 selections; Aurora 2/3 | Not tested |
| Android 15 LineageOS | CruxCoach on another device | All 52 selections; Aurora 2/3 | Not tested |
| iPhone (signed build required) | CruxCoach on another device | All 52 selections; Aurora 2/3 | Not tested |
| Each of the above | Official Kilter/Tension/Grasshopper/Decoy/So iLL/Touchstone apps | Each available layout/size | Not tested |
| Each of the above | Official MoonBoard app | 2016, Masters 2017/2019, 2024, Mini 2020 | Not tested |
| Each of the above | eWalls | Quantum XL/L/M/S Fitness/Belay | Not tested |
| Linux | Existing CruxCoach and official-app checks | Existing single/multi-instance behavior | No new physical test |

For each configuration:

1. Start with Bluetooth disabled, deny/grant permission, then enable and Start.
   Confirm errors and recovery; verify the full advertised name and service UUID.
2. Connect from the controller, verify GATT service/characteristic properties,
   send known start/hand/finish/foot holds, compare positions against the app.
3. Send a long climb, then a different climb and a clear command. No stale holds.
4. Disconnect mid-frame and reconnect. On iOS unsubscribed UART, Stop/Start first.
   Confirm no partial prior frame corrupts the next climb.
5. Quantum: subscribe fff1, read 41-byte fff5, negotiate MTU >=44, activate two
   users/routes, swipe/remove one, read fff4 with offsets, clear. Repeat at default
   MTU to confirm the visible notification-size limitation and readable state.
6. Attempt a second controller; verify ownership limit rather than mixed streams.
7. Rotate/resize, background, return, switch boards using runtime selection.
   Verify old services are removed and Android's prior adapter name restored.
8. Repeat using official apps; scanner behavior is an independent acceptance gate.

Use existing `tests/test_ble_mock_client.py` where applicable for a real external
central. A successful mock-client run is not a substitute for official-app tests.
