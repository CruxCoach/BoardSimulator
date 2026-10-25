# BoardSimulator — Consolidated software BLE simulator for all CruxCoach boards

Pure-software simulation of **all nine interactive boards** from
CruxCoach via real Bluetooth Low Energy (BLE) peripherals. Linux, Android and
iOS are developed in this repository; Linux uses the CLI/Tk interface and the
mobile apps use native BLE with a shared offline board view:

| Board | Protocol | Layouts | Role IDs |
|-------|----------|---------|----------|
| **Kilter** | Aurora | Original, Homewall | 12–15 / 42–45 |
| **Tension** | Aurora | TB1, TB2 (Mirror), TB2 (Spray) | TB1: 1–4, TB2: 5–8 |
| **Grasshopper** | Aurora | Grasshopper 2020 | 1–4 |
| **Decoy** | Aurora | Dungeon Trainer, Dots | 1–4 |
| **So iLL** | Aurora | Summer 2024 | 1–4 |
| **Touchstone** | Aurora | Winter 2020 | 1–4 |
| **MoonBoard** | NUS/ASCII | 2016, Masters 2017, Masters 2019, 2024, Mini 2020 | Token-based |
| **Quantum Board** | CRC16/MODBUS + legacy JSON | XL, L, M, S, Belay | Start / step / finish / route |
| **Shiny Wall** | ASCII state vector (`-`, `+<digit per hold>.`) | Standard (st1), Full (st4), Max (st2), Max Full (st5), Mini (st0) | Codes 2 / 4 / 6 / 8 |

The PC acts as a BLE peripheral via the local Bluetooth adapter (BlueZ)
and accepts connections from the respective official app or from
CruxCoach. Climb frames that are sent are decoded and visualized live —
either in a Tkinter GUI (board image or board photo) or in headless mode
as an ASCII grid on stdout.

## Platform support and test builds

| Platform | Implementation | Build/install status | Simultaneous boards |
|---|---|---|---|
| Linux | Python, BlueZ, Tk or headless | Existing runtime; 471 automated tests | Existing multi-adapter / two-instance modes |
| Android 9+ | Native Java GATT server/advertiser, offline WebView | Debug APK built/linted; Android 15 install, advertising and live RX checked | Two experimental disjoint GATT endpoints; overlapping profiles pending |
| iOS 15+ | Native Swift CoreBluetooth, offline WKWebView | Unsigned device and simulator compilation in macOS CI; signing/device tests pending | Two experimental disjoint GATT endpoints; overlapping profiles pending |

All **52 board/layout/size selections** are exported from the Linux registry,
including Quantum XL/L/M/S Fitness/Belay. Aurora API 2 and 3 are selectable.
Controllers (CruxCoach or official apps) ordinarily run on another physical
device. Mobile official-app discovery and physical BLE interoperability are
**not yet verified**. No mock radio replaces missing peripheral support.

Download test artifacts from [GitHub Actions](https://github.com/CruxCoach/BoardSimulator/actions/workflows/ci.yml):
`boardsimulator-android-debug` contains the installable debug APK;
`boardsimulator-ios-unsigned` contains unsigned iPhone and simulator app ZIPs,
**not a signed, installable iPhone IPA**. Artifact download requires GitHub login.
The [build/install and hardware validation guide](docs/mobile-testing.md) explains
local builds, iOS signing with no local Mac, and the outstanding physical matrix.
The [architecture assessment](docs/mobile-architecture.md) documents native API
limits, foreground lifecycle, advertisement names and Quantum MTU requirements.

Mobile quick build (Python requirements, JDK 17, Android SDK 35):

```sh
python tools/export_mobile.py
cd mobile/android
./gradlew assembleDebug lintDebug
# app/build/outputs/apk/debug/app-debug.apk
```

Changing board/layout/size rebuilds that mobile endpoint at runtime. Backgrounding
stops the mobile peripherals; Start resumes with a new session. Android temporarily changes the
adapter name and restores it on normal Stop. iOS advertisement placement/name
truncation is controlled by the OS. **Full Linux feature parity is not achieved.** Two panels with real BLE endpoints
are implemented experimentally for Quantum + Aurora/MoonBoard. Duplicate or
shared UART profiles remain rejected because advertising-to-link routing is
missing. See the [feature matrix, API evidence, probes and alternatives](docs/mobile-parity.md).
Exclusive and multi-connect are separate controls. Android single-board exclusive
stops advertising on connection; iOS can only stop on first ATT activity and
requires manual resume after disconnect. Updated native modes await physical
testing; the attached Android device is currently off limits by user instruction.

## Monorepo layout

```text
main.py, protocols/, ble/, render/   Existing Linux runtime
boards.py, data/, assets/            Authoritative registry, geometry, original assets
mobile/shared/                      Offline UI and shared JavaScript decoders
mobile/android/                     Java BLE peripheral and Gradle app
mobile/ios/                         Swift BLE peripheral and XcodeGen project
mobile/tests/                       Python-vs-JavaScript conformance checks
tools/export_mobile.py              Generate mobile catalog/geometry/GATT/resources
tools/mobile_fixtures.py            Generate independent Python protocol traces
.github/workflows/ci.yml             Linux, Android and macOS/iOS builds
```

GitHub is the primary repository. Codeberg history and branches were retained;
automatic mirroring is not configured.

## Linux requirements

- **Python** 3.10+
- **Linux** with BlueZ
- **Bluetooth adapter** with BLE peripheral and Extended Advertising support
  (only for live BLE; `--list` and the tests run without one). One adapter can
  simulate one board normally or two virtual boards in multiplex mode.
- **System packages:**
  ```bash
  sudo apt install bluez bluetooth python3-tk
  ```

## Installation

### Quick start (Linux Mint / Ubuntu / Debian)

Copy-paste ready — clone and run the bundled setup script. It installs
the apt packages, creates the venv, installs the dependencies and runs a
`--list` smoke test:

```bash
git clone https://github.com/CruxCoach/BoardSimulator.git
cd BoardSimulator
./setup.sh
```

`setup.sh` asks once for the sudo password (for the apt packages
`git bluez bluetooth python3-venv python3-tk`). After that, enable BlueZ
(see below) and everything is ready to go.

### Manual (alternative)

```bash
cd BoardSimulator
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configure BlueZ

BLE peripheral mode requires BlueZ's experimental mode
(`/etc/bluetooth/main.conf`):

```ini
[General]
Experimental = true
```

Then: `sudo systemctl restart bluetooth`

## Usage

### Starting the simulator

BLE peripheral mode requires root privileges and a Bluetooth adapter:

```bash
# Kilter Board Original, 12x12 with kickboard, GUI
sudo venv/bin/python main.py --board kilter

# Kilter Homewall 10x10
sudo venv/bin/python main.py --board kilter --layout homewall

# Two distinct Kilter realms on the same built-in controller
sudo venv/bin/python main.py --board kilter --layout homewall --instances 2

# Tension Board 2 (Mirror), 12x12
sudo venv/bin/python main.py --board tension --layout tb2

# MoonBoard Mini 2020
sudo venv/bin/python main.py --board moonboard --layout mini-2020

# Quantum XL with the original eWalls board image and diode overlay
sudo venv/bin/python main.py --board quantum --layout xl

# Shiny Wall Standard (st1) as "ShinyWall_STP_0001", every write logged
sudo venv/bin/python main.py --board shinywall --headless

# …with an exact name for the official app, no service UUID advertised,
# "OK" notifications, and strict vector length
sudo venv/bin/python main.py --board shinywall --layout st5 \
    --name "ShinyWall_STMF_ABC123" --no-advertised-uuid --shiny-ack \
    --shiny-length strict --headless

# So iLL in headless mode
sudo venv/bin/python main.py --board soill --headless

# A Kilter-built box (since mid-2025): bare name "Kilter Board", API 2,
# write-with-response only — here as the 16x12 Super Wide
sudo venv/bin/python main.py --board kilter --size 28 --controller kilter-built

# …and drop chunks closer than 100 ms, to prove an app's pacing
sudo venv/bin/python main.py --controller kilter-built --strict-pacing

# Two new Kilter walls in one gym (both "Kilter Board"); a released wall
# re-advertises only after 5 s, like real boxes — does the app reconnect
# to the wall it released, or grab the neighbour?
sudo venv/bin/python main.py --controller kilter-built --instances 2 \
    --readvertise-delay 5

# List all boards/layouts/sizes (no BLE, no root needed)
python main.py --list
```

| Option | Default | Description |
|--------|---------|-------------|
| `--board` | `kilter` | `kilter` `tension` `grasshopper` `decoy` `soill` `touchstone` `moonboard` `quantum` `shinywall` |
| `--layout` | first layout | e.g. Kilter: `original` `homewall`; MoonBoard: `2016` … `mini-2020`; Quantum: `xl` `l` `m` `s` `belay`; Shiny Wall: `st1` `st4` `st2` `st5` `st0` |
| `--size` | layout default | Aurora `product_size_id` (see `--list`) — Aurora boards only |
| `--api-level` | `3` | Aurora protocol version (`2` or `3`), suffix `@N` in the BLE name — Aurora boards only |
| `--serial` | `0001` | Serial number, suffix `#serial` in the BLE name — Aurora boards only |
| `--controller` | `aurora` | LED box generation: `aurora` (classic, `Name#serial@apiLevel`, writes with and without response) or `kilter-built` (bare name, API 2, write-with-response only, chunks closer than 100 ms are reported) — Aurora boards only |
| `--strict-pacing` | off | With `--controller kilter-built`: drop chunks that arrive sooner than 100 ms after the previous one |
| `--readvertise-delay` | `0` | Seconds a board stays silent after its last central left (real Aurora boxes often take > 4 s) — exclusive connections only |
| `--instances` | `1` | Simulate one or two virtual boards on the selected adapter |
| `--second-board` | same as board 1 | Initial board for slot 2 |
| `--second-layout` | same/default | Initial layout for slot 2 |
| `--second-size` | layout default | Initial Aurora size for slot 2 |
| `--second-serial` | incremented | Distinct Aurora serial for slot 2 |
| `--adapter` | `hci0` | Bluetooth controller to drive (`hci0`, `hci1`, … — see `hciconfig`) |
| `--window-height` | `750` (`520` per board with two) | Board height the GUI window opens with — the window is resizable regardless |
| `--fullscreen` | off | Open the GUI in fullscreen (F11 toggles, Escape leaves) |
| `--headless` | off | ASCII grid on stdout instead of GUI (also: `BOARDSIM_HEADLESS=1`) |
| `--att-log` / `--no-att-log` | on for the Shiny Wall | Log every ATT PDU and link event from the HCI monitor channel: MTU exchange, discovery, reads, subscriptions, errors — also for attributes the simulator does not offer |

Shiny Wall only (each aborts with an error on other boards):

| Option | Default | Description |
|--------|---------|-------------|
| `--name` | `ShinyWall_<code>_0001` | Exact advertised name, at most 29 bytes (more fails at startup). Codes: `STP` st1, `STF` st4, `STM` st2, `STMF` st5; st0 advertises `Shiny Wall ST0 (ShinyMini)` |
| `--service-uuid` | `4fafc201-1fb5-459e-8fcc-c5c9c331914b` | Service holding the write characteristic `beb5483e-36e1-4688-b7f5-ea07361b26a8` |
| `--no-advertised-uuid` | off | Advertise flags + name only |
| `--shiny-holds` | variant | Holds in a state vector (st1 311, st4 401, st2 475, st5 611, st0 239 — provisional) |
| `--shiny-length` | `lenient` | Wrong vector length: `lenient` applies what fits and warns, `strict` rejects it |
| `--shiny-codes` | `reject` | Digits other than 0/2/4/6/8: `reject` leaves the hold off, `accept` shows it as unknown |
| `--shiny-mtu` | – | ATT MTU of the simulated controller; longer writes are dropped (pair with `ExchangeMTU`, see below) |
| `--shiny-delay` | `0` | Processing delay per write in ms (blocks the BLE loop, delays the write response) |
| `--shiny-on-disconnect` | `keep` | LEDs when the last central leaves: `keep` or `clear` |
| `--shiny-props` | `read,write,write-without-response,notify` | Properties of the write characteristic |
| `--shiny-ack` | off | Notify `OK` after every understood message (the official app waits for it in traverses) |
| `--shiny-notify-uuid` | – | Extra notify-only characteristic; `--shiny-ack` then notifies there |
| `--shiny-geometry` | `data/shinywall_geometry_v1.json` if present | Hold positions (`cruxcoach-shinywall-geometry-v1`); otherwise index order on the grid |

Aurora-specific options on the MoonBoard, Quantum or Shiny Wall abort with a
clear error message — as does a start without a BlueZ/Bluetooth adapter
(**fail-fast** instead of silently hanging).

After starting:
1. The BLE peripheral advertises — Aurora boards as
   `<BoardName>#<serial>@<apiLevel>` (e.g. `Kilter Board#0001@3`),
   the MoonBoard as the bare `MoonBoard`, a Shiny Wall as
   `ShinyWall_<code>_<id>`
2. The official app or CruxCoach can connect
3. Climbs that are sent are visualized live — with the **board's own**
   role colors (Kilter e.g. middle=cyan/finish=magenta, So iLL
   middle=magenta/finish=white/foot=cyan)

### Window size (demos, projectors)

The GUI window is freely resizable and the **whole board view scales with
it** — the board image (or MoonBoard photo) is re-rendered at the new
size, hold rings are repositioned, and their radius and stroke width grow
along with the board. The aspect ratio is kept, so a window that is wider
than the board letterboxes instead of distorting it. In two-board mode
both panels scale together.

```bash
# Fullscreen — F11 toggles it at runtime, Escape leaves it
sudo venv/bin/python main.py --board kilter --fullscreen

# Or open at a fixed, larger size
sudo venv/bin/python main.py --board kilter --window-height 1200
```

While the window is being dragged the background is resampled cheaply and
re-rendered sharply once the size settles, so resizing stays responsive
even at full screen.

### Two boards at once (one adapter, two realms)

Choose **Simulation → 2 boards** in the GUI, or start directly with:

```bash
sudo venv/bin/python main.py --board kilter --layout homewall \
    --instances 2 --serial a001 --second-serial b002
```

The simulator programs two connectable hardware advertising sets with stable,
different random BLE addresses. Both boards are visible at the same time.
Linux HCI events identify which advertising set accepted each connection;
incoming GATT writes are then routed permanently to the corresponding board
decoder and GUI panel. In the default `single` connection mode, a connected
slot is no longer advertised; the other slot remains available. The adapter
is made non-pairable while this mode runs, matching the boards' unpaired GATT
workflow and avoiding desktop pairing prompts.

Both slots initially use the selected board/layout. Aurora serials default to
`0001` and `0002`; both boards are displayed side by side and each panel has
its own Board/Layout/Size controls. Changing
the simulation count or either board rebuilds BLE and disconnects existing
clients. Controller firmware still determines how many parallel LE links it
can maintain. Two-board mode requires Extended Advertising hardware offload
with at least three controller handles (BlueZ keeps handle 0; the simulator
uses handles 1 and 2). Check it with:

```bash
sudo hcitool -i hci0 cmd 0x08 0x003b
busctl get-property org.bluez /org/bluez/hci0 \
    org.bluez.LEAdvertisingManager1 SupportedFeatures
```

The final byte of the HCI result is the supported-set count; `HardwareOffload`
must be listed by BlueZ. Your controller's `14` hex means 20 sets and meets
the requirement comfortably.

Both bundled Kilter layouts render their board image; the geometry-dot fallback
is used only for board/size combinations without an image asset.

The existing two-adapter setup remains available: run one process per adapter
with `--adapter hci0` and `--adapter hci1` when maximal hardware isolation is
desired.

### Switching boards at runtime

Each GUI has a **board bar** at the top with dropdowns for **Board**,
**Layout**, (for Aurora boards) **Size**, connection behavior, and
**Simulation** (`1 board` / `2 boards`). A selection switches the
simulated board live — the process keeps running, and the window and BLE
peripheral are cleanly rebuilt for the new board (this also covers a
switch between the Aurora ↔ MoonBoard protocol families, since the BLE
name and GATT profile change). In `--headless` mode there is no bar;
there you choose the board at startup.

### Running the test client

In a separate terminal (while `main.py` is running):

```bash
source venv/bin/activate
pip install bleak   # only needed for the mock client
python tests/test_ble_mock_client.py kilter      # or tension, moonboard, ...
python tests/test_ble_mock_client.py quantum
python tests/test_ble_mock_client.py kilter --kilter-built   # bare name, API 2, paced
python tests/test_ble_mock_client.py shinywall --layout st1  # like the official app
python tests/test_ble_mock_client.py shinywall --name ShinyWall_STP_ABC123 --command
```

The test client scans using the same naming rules as CruxCoach, connects
and sends a sample climb — for Aurora boards three holds in the
respective board's colors, for the MoonBoard an ASCII frame like the one
from CruxCoach's `MoonBoardFrameEncoder`, for the Shiny Wall `-` and a state
vector paced like the official app (100 ms before a message, 50 ms between
its chunks of MTU − 5 bytes).

### Unit tests

The tests require neither root nor a Bluetooth adapter:

```bash
venv/bin/python -m pytest tests/ -q
```

## Architecture

```
main.py                 CLI → Session → BLE peripheral + renderer
boards.py               Registry: 9 boards, 4 protocol families
protocols/
  session.py            Family wiring: GATT profile, decoder, state
  aurora_decoder.py     Aurora binary protocol (API level 2 + 3)
  aurora_encoder.py     Reference encoder (CruxCoach BoardPacketEncoder port)
  moonboard.py          MoonBoard ASCII frames (NUS)
  quantum.py            Quantum CRC16/MODBUS + legacy JSON
  shinywall.py          Shiny Wall ASCII messages (decoder + reference encoder)
  shinywall_session.py  Shiny Wall session: write context, limits, logging
ble/
  adapter.py            Adapter name → D-Bus path, match rule, hcitool -i
  peripheral.py         BlueZ D-Bus peripheral + fail-fast preflight
  multiplex.py          Two simultaneous hardware identities on one controller
  hci_monitor.py        Advertising-handle → connection event mapping
  att_log.py            ATT/link log from the HCI monitor channel (--att-log)
  gatt.py               GATT profile → D-Bus objects
  advertising.py        Extended-Advertising HCI helpers
render/                 GUI + headless per family
board_geometry.py       LED↔hole coordinates, edges, roles (SQLite)
quantum_geometry.py     Quantum address/coordinate lookup
shinywall_geometry.py   Shiny Wall hold positions (geometry file or index order)
board_state.py          Thread-safe board state per family
role_colors.py          Wire color → board-local role
data/<brand>.sqlite3    Trimmed official board DBs
data/quantum_geometry.json  route/user-free diode fixture
assets/<brand>/         Board images / photos + coordinate maps
```

The BLE peripheral layer (BlueZ/D-Bus) is **shared**; only the GATT
shape/advertising (declarative `GattProfile`) and the decoder differ per
protocol family.

## Board identity & detection

### Aurora family (Kilter + 5 boards)

All six boards speak the same Aurora protocol — the brand identity lives
exclusively in the advertised name:

- **Official brand apps** accept any device whose name **contains** their
  filter substring (case-sensitive): `Kilter`, `Tension`,
  `Grasshopper`, `Decoy`, `So iLL`, `Touchstone`. API level from the
  `@N` suffix (default 2), serial number from the `#…` suffix.
- **CruxCoach 0.2.0** parses `Name#serial@apiLevel`, normalizes the name
  part (lowercase, strip spaces/hyphens) and matches the brand
  **prefix**; Kilter is the fallback for any unrecognized Aurora name.
  CruxCoach 0.2.4 also accepts a bare name with an Aurora family prefix
  or the advertised discovery service.

**Two controller generations** (`--controller`):

| | `aurora` (classic box) | `kilter-built` (since mid-2025) |
|---|---|---|
| Advertised name | `Kilter Board#0001@3` | `Kilter Board` (no serial, no API level) |
| API level | `@N` suffix, default 3 here | 2 (what apps assume without `@3`) |
| RX characteristic | write + write-without-response | write (with response) only |
| Pacing | none expected | the Kilter app writes 20-byte chunks 100 ms apart; faster chunks are logged, `--strict-pacing` drops them |

A write without response to the write-only RX is logged once per central:
Android delivers it, but iOS drops it on the sending side, so an app that
relies on it stays dark on iOS. Two `kilter-built` instances advertise the
same name and differ only by address — like two new boards in one gym.
The behaviour is taken from Boardsesh's field telemetry and the official
Kilter app; it is not a capture of a physical Kilter-built box.

| Purpose | UUID |
|---------|------|
| Advertising/discovery service (empty) | `4488B571-7806-4DF6-BCFF-A2897E4953FF` |
| UART service (data transfer) | `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` |
| RX characteristic (app writes climbs) | `6E400002-B5A3-F393-E0A9-E50E24DCCA9E` |

**Packet format:**
```
[0x01] [data length] [checksum] [0x02] [position code] [hold data...] [0x03]
```
API 3: 3 bytes/hold (16-bit LED position + RGB332); API 2: 2 bytes/hold
(10-bit position, 2 bits/channel, 18 W power budget). Checksum:
`(~sum) & 0xFF`. 20-byte BLE chunks are reassembled; an empty packet
clears the board.

### MoonBoard

A MoonBoard exposes **only** the Nordic UART service and advertises its
UUID itself; the name is the bare `MoonBoard` (prefix match in app and
CruxCoach). The TX characteristic (`6E400003-…`) is a notify stub.

**Frame format:** `l#<token><pos>,<token><pos>,...#`, e.g.
`l#S0,P1,E197#`. Tokens (case-insensitive): `S`=start, `R`/`P`=hand,
`L`=left hand, `M`=match, `F`=foot, `E`=finish. `<pos>` is the
0-indexed serial strip position on the serpentine-wired LED strip (even
columns bottom to top, odd columns top to bottom; column height 18, or
12 on the Mini 2020). The `~` config variants (`~D…#` = aux LEDs above
the holds) are decoded as well.

### Quantum Board

eWalls 2.0.14 scans `QB_<12 hex>` / `QBB_<12 hex>` names, prefers the
Bluetooth-base `ffe0` service (`fff0` is its legacy
fallback), writes to `fff2`, subscribes to `fff1`, reads state from `fff4` and
probes the 41-byte board identity at `fff5`. The simulator exposes that current
shape and a model-specific identity record.

Current frames start with device address `01`, then command and payload, and end
with a big-endian CRC-16/MODBUS word. UUIDs are exactly 16 raw bytes. The
simulator implements the 2.0.14 commands (`41`–`45`, `47`, `64`), arbitrary
GATT fragmentation and 92-diode chunks. Removed commands, 1.44's little-endian
CRC/ASCII IDs and JSON remain isolated in the explicit legacy decoder. Errors
never mutate the board; reconnect clears only a partial frame.

Successful writes update the simulated route roster. The simulator publishes a
parser-compatible event through `fff1` and keeps the authoritative `0x47`
snapshot readable through `fff4`; the original app's normal route-play duration
is `0xffff`. Multiplex mode keeps `fff4` state and `fff5` identity isolated per
requesting device. If two Quantum instances share `fff1`, its notification is
suppressed because BlueZ cannot address it to only one central; this prevents
cross-board roster leaks while preserving each board's readable state. Fault
profiles emit Modbus exceptions without mutating LEDs.

These behaviours model the statically recovered eWalls 2.0.14 app contract.
There is no physical Quantum hardware capture in the repository, so exact
firmware responses and timing are deliberately not claimed. The evidence and
remaining boundaries are documented in [the Quantum E2E guide](docs/quantum-e2e.md).

### Shiny Wall

The controller is an ESP32. It exposes one characteristic,
`beb5483e-36e1-4688-b7f5-ea07361b26a8`, which the official app looks up across
all services (the simulator puts it in the ESP32 example's service
`4fafc201-…`). Standard walls advertise `ShinyWall_<code>_<id>` (`STP` Standard
st1, `STF` Full st4, `STM` Max st2, `STMF` Max Full st5), the Mini
`Shiny Wall ST0 (ShinyMini)`.

Messages are ASCII and follow `<command><payload>.`, split into writes of
MTU − 5 bytes:

| Message | Meaning | Simulator |
|---------|---------|-----------|
| `-` | Clear the wall (no dot) | applied |
| `+<d0><d1>…<dN>.` | One digit per hold 0…hold count − 1: 0 off, 2 start, 4 hand, 6 foot, 8 top | applied |
| `*<x>.` | Single hold / game command | logged |
| `f<n>.` | Init foot LEDs, sent once after connecting when the wall has it set | logged |
| anything else | Unknown command; the old draft `.c0+c1+….` is reported as such | logged |

The official app (from its BLE code; no wall and no firmware were available)
writes with response, sends `-` first, waits 100 ms before each message and
50 ms between its chunks, and asks for MTU 512 after connecting. It waits for
an `OK` notification on the same characteristic only in traverses
(`--shiny-ack`). The simulator's defaults are assumptions where the firmware
is unknown: it keeps a partial message across writes, `-` always clears, a
wrong vector length is applied as far as it fits, and LEDs stay lit after a
disconnect. Each of these has a flag to test the stricter behaviour.

Hold counts (st1 311, st4 401, st2 475, st5 611, st0 239) are the catalogue's
highest hold numbers plus one, as in CruxCoach. The official app sends as
many digits as the wall's hold list in its backend spans, so a `LENGTH` line
in the log shows the real count of that wall. Positions: without a geometry
file the simulator draws the holds in index order on the variant's grid
(12×35, 18×35, Mini 12×29) and says so in the legend.

### Roles & colors (board-local!)

| Board | start | middle | finish | foot |
|-------|-------|--------|--------|------|
| Kilter (Original + Homewall) | `00FF00` | `00FFFF` | `FF00FF` | `FFA500` |
| Tension / Grasshopper / Decoy / Touchstone | `00FF00` | `0000FF` | `FF0000` | `FF00FF` |
| So iLL | `00FF00` | `FF00FF` | `FFFFFF` | `00FFFF` |

The simulator resolves decoded colors back to the role via the board's
own palette (GUI: screen color of the role, headless: role letter).
MoonBoard role colors come from the token (start=green, hand=blue,
finish=red, foot=cyan, …). The Shiny Wall's codes are drawn as start green,
hand blue, foot yellow, top red (the simulator's own neutral colours).

## Data provenance

`data/<brand>.sqlite3` are trimmed copies of the official board
databases (geometry/identity only). The five Aurora-family DBs are
generated by `tools/build_data.py` from a local source workspace;
`data/kilter.sqlite3` is generated by `tools/trim_kilter_db.py` from a
full Kilter DB (restricted to Original + Homewall).
`assets/kilter/board_10.webp` is composited from the two hold layers
(bolt-ons + screw-ons) of the KilterSimulator; `board_21.webp` is the
byte-identical CruxCoach Homewall asset. The MoonBoard photos + coordinate
maps come from the MoonSimulator. End users need none of these scripts.

`data/quantum_geometry.json` is generated by
`tools/build_quantum_geometry.py` from the public authorised Quantum SQLite
snapshot. It retains only the two controller address forms, hold class and
coordinates for each model; routes, users and setters are excluded. The five
files in `assets/quantum/` are byte-identical `board-small` assets from eWalls
2.0.14. Diode overlays use the exact square-viewport transform recovered from
that app's renderer. Catalog types are XL=`big`, L=`medium`, M=`small`, S
Fitness=`xsmall` and Belay=`belay`. The image/coordinate contract is verified;
physical controller timing and wiring remain `hardware_verified=false`.

## Testing with the official Shiny Wall app

Goal: prove the simulator speaks the controller's protocol by letting the
official Shiny Wall app (Android) drive it, then check CruxCoach against the
same simulator. As far as its code shows, the official app needs:

- **a wall selected in the app** (it connects to that wall's controller and
  sends what the wall's hold list says), so most likely an account. Pick a
  Standard wall of the variant you simulate.
- **a matching name.** The app scans without a filter, waits about 1 s and
  connects to the first device whose name *contains* the controller name
  stored for that wall; when the wall has none stored it looks for
  `ShinyWall_`. So either set `--name` to the stored name (or any name that
  contains it), or leave the default `ShinyWall_<code>_0001`. Keep other
  Shiny Wall controllers and simulators out of range — the app takes the
  first match.
- no service UUID filter. Pairing is not expected; if the app asks for it,
  the ATT log shows `SMP … pairing request`. Grant the app's Bluetooth (and,
  on older Android, location) permissions.

### Linux

```bash
sudo venv/bin/python main.py --board shinywall --layout st1 \
    --name "ShinyWall_STP_ABC123" --headless 2>&1 | tee shiny-official.log
# variants of the same test:
#   --layout st4|st2|st5|st0          the wall's variant
#   --shiny-holds 476                 the count the LENGTH line reported
#   --no-advertised-uuid              advertise flags + name only
#   --service-uuid <uuid>             another service for the characteristic
#   --shiny-ack                       answer "OK" (traverses)
```

BlueZ negotiates the MTU itself (`/etc/bluetooth/main.conf`, `[GATT]`
`ExchangeMTU`, default 517). To see how the app behaves at a small MTU, set
`ExchangeMTU = 23`, restart bluetooth and start with `--shiny-mtu 23`.

### Android (BoardSimulator app)

Install the `boardsimulator-android-debug` APK on a second phone (the one
running the official app cannot also be the wall). Choose **Board: Shiny Wall**,
the layout, type the name into **Advertised name**, optionally untick
**Advertise service UUID** or tick **Reply OK**, then **Start BLE**. Keep the
app in front. Follow the log on a computer with

```bash
adb logcat -s BoardSimUI BoardSimWire
```

### What to check, and the log lines that show it

1. The app connects: `LINK <address> connected …` and `Central <address>
   connected` (Linux), `Connected · <address>` (Android).
2. MTU: `MTU request 512` / `ATT MTU now …` (Linux ATT log), `MTU …`
   (Android).
3. Discovery and any read or subscription it does: `discover services`,
   `READ …`, `SUBSCRIBE notify on beb5483e-…` (Linux), `Subscribed · …`
   (Android). Requests for attributes the simulator lacks show as
   `ERROR for …` warnings.
4. Optional `f<n>.` right after connecting: `FOOT init foot LEDs …`.
5. Send a route from the app: `RX 1 B (#…, request, …): '-'`, `CLEAR`, then
   the vector in writes of MTU − 5 bytes about 50 ms apart and
   `STATE <n> codes applied — start … hand … foot … top …`. The holds light
   in the window (index numbers on each ring). A `LENGTH` warning means the
   wall in the app has another hold count — note it, restart with
   `--shiny-holds`.
6. Clear / leave the route: `CLEAR`. Single holds or games: `SINGLE …`.
7. Disconnect: `LINK … disconnected (reason …)`, `Link ended: … stay lit`.

Any `GARBAGE`, `LEGACY`, `COMMAND`, `INVALID`, `UNKNOWN-CODE` or `ABORT`
line is a finding: it names the bytes that did not fit the protocol.

Then run CruxCoach against the same simulator: its scanner lists
`ShinyWall_STP_…` as a Shiny Wall; sending a route must produce the same
`CLEAR` + `STATE` lines with the route's holds.

## Troubleshooting

### "Bluetooth unavailable" at startup
The fail-fast preflight did not find BlueZ or the requested adapter:
- BlueZ installed/started? `systemctl status bluetooth`
- Adapter present and up? `hciconfig` → `sudo hciconfig hci0 up`
- Right controller? The error lists the adapters BlueZ actually has —
  pass one of them via `--adapter` (default `hci0`).
- On machines without Bluetooth, only `--list` and the tests run.

### App doesn't find the board
- Is the simulator running as root? (`sudo venv/bin/python main.py …`)
- BlueZ `Experimental = true` set and the service restarted?
- `hcitool` installed? (part of `bluez` — needed for the advertising
  rewrite)

### The Shiny Wall log shows `LENGTH`
The app's vector has another length than the simulated variant. The official
app sends one digit per hold of the selected wall's hold list: start with
`--shiny-holds <n>` (n = the count in the warning) or the matching `--layout`.

### Connection drops after the first climb
Normal on some Android versions — the simulator restarts advertising
automatically; just reconnect.

## Note

Real BLE verification against the official apps/CruxCoach requires a
machine with a Bluetooth adapter and must be done manually; the unit
tests cover protocol, geometry and registry but do not replace a device
test.
