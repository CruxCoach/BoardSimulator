# BoardSimulator — Consolidated software BLE simulator for all CruxCoach boards

Pure-software simulation of **all eight interactive board families** from
CruxCoach on Linux via Bluetooth Low Energy (BLE), in a single
codebase and selectable per CLI:

| Board | Protocol | Layouts | Role IDs |
|-------|----------|---------|----------|
| **Kilter** | Aurora | Original, Homewall | 12–15 / 42–45 |
| **Tension** | Aurora | TB1, TB2 (Mirror), TB2 (Spray) | TB1: 1–4, TB2: 5–8 |
| **Grasshopper** | Aurora | Grasshopper 2020 | 1–4 |
| **Decoy** | Aurora | Dungeon Trainer, Dots | 1–4 |
| **So iLL** | Aurora | Summer 2024 | 1–4 |
| **Touchstone** | Aurora | Winter 2020 | 1–4 |
| **MoonBoard** | NUS/ASCII | 2016, Masters 2017, Masters 2019, Mini 2020 | Token-based |
| **Quantum Board** | CRC16/MODBUS + legacy JSON | XL, L, M, S, Belay | Start / step / finish / route |

The PC acts as a BLE peripheral via the local Bluetooth adapter (BlueZ)
and accepts connections from the respective official app or from
CruxCoach. Climb frames that are sent are decoded and visualized live —
either in a Tkinter GUI (board image or board photo) or in headless mode
as an ASCII grid on stdout.

## Requirements

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
git clone https://codeberg.org/CruxCoach/BoardSimulator.git
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

# So iLL in headless mode
sudo venv/bin/python main.py --board soill --headless

# List all boards/layouts/sizes (no BLE, no root needed)
python main.py --list
```

| Option | Default | Description |
|--------|---------|-------------|
| `--board` | `kilter` | `kilter` `tension` `grasshopper` `decoy` `soill` `touchstone` `moonboard` `quantum` |
| `--layout` | first layout | e.g. Kilter: `original` `homewall`; MoonBoard: `2016` … `mini-2020`; Quantum: `xl` `l` `m` `s` `belay` |
| `--size` | layout default | Aurora `product_size_id` (see `--list`) — Aurora boards only |
| `--api-level` | `3` | Aurora protocol version (`2` or `3`), suffix `@N` in the BLE name — Aurora boards only |
| `--serial` | `0001` | Serial number, suffix `#serial` in the BLE name — Aurora boards only |
| `--instances` | `1` | Simulate one or two virtual boards on the selected adapter |
| `--second-board` | same as board 1 | Initial board for slot 2 |
| `--second-layout` | same/default | Initial layout for slot 2 |
| `--second-size` | layout default | Initial Aurora size for slot 2 |
| `--second-serial` | incremented | Distinct Aurora serial for slot 2 |
| `--adapter` | `hci0` | Bluetooth controller to drive (`hci0`, `hci1`, … — see `hciconfig`) |
| `--window-height` | `750` (`520` per board with two) | Board height the GUI window opens with — the window is resizable regardless |
| `--fullscreen` | off | Open the GUI in fullscreen (F11 toggles, Escape leaves) |
| `--headless` | off | ASCII grid on stdout instead of GUI (also: `BOARDSIM_HEADLESS=1`) |

Aurora-specific options on the MoonBoard or Quantum abort with a clear error
message — as does a start without a BlueZ/Bluetooth adapter
(**fail-fast** instead of silently hanging).

After starting:
1. The BLE peripheral advertises — Aurora boards as
   `<BoardName>#<serial>@<apiLevel>` (e.g. `Kilter Board#0001@3`),
   the MoonBoard as the bare `MoonBoard`
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
```

The test client scans using the same naming rules as CruxCoach, connects
and sends a sample climb — for Aurora boards three holds in the
respective board's colors, for the MoonBoard an ASCII frame like the one
from CruxCoach's `MoonBoardFrameEncoder`.

### Unit tests

The tests require neither root nor a Bluetooth adapter:

```bash
venv/bin/python -m pytest tests/ -q
```

## Architecture

```
main.py                 CLI → Session → BLE peripheral + renderer
boards.py               Registry: 8 boards, 3 protocol families
protocols/
  session.py            Family wiring: GATT profile, decoder, state
  aurora_decoder.py     Aurora binary protocol (API level 2 + 3)
  aurora_encoder.py     Reference encoder (CruxCoach BoardPacketEncoder port)
  moonboard.py          MoonBoard ASCII frames (NUS)
  quantum.py            Quantum CRC16/MODBUS + legacy JSON
ble/
  adapter.py            Adapter name → D-Bus path, match rule, hcitool -i
  peripheral.py         BlueZ D-Bus peripheral + fail-fast preflight
  multiplex.py          Two simultaneous hardware identities on one controller
  hci_monitor.py        Advertising-handle → connection event mapping
  gatt.py               GATT profile → D-Bus objects
  advertising.py        Extended-Advertising HCI helpers
render/                 GUI + headless per family
board_geometry.py       LED↔hole coordinates, edges, roles (SQLite)
quantum_geometry.py     Quantum address/coordinate lookup
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

### Roles & colors (board-local!)

| Board | start | middle | finish | foot |
|-------|-------|--------|--------|------|
| Kilter (Original + Homewall) | `00FF00` | `00FFFF` | `FF00FF` | `FFA500` |
| Tension / Grasshopper / Decoy / Touchstone | `00FF00` | `0000FF` | `FF0000` | `FF00FF` |
| So iLL | `00FF00` | `FF00FF` | `FFFFFF` | `00FFFF` |

The simulator resolves decoded colors back to the role via the board's
own palette (GUI: screen color of the role, headless: role letter).
MoonBoard role colors come from the token (start=green, hand=blue,
finish=red, foot=cyan, …).

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

### Connection drops after the first climb
Normal on some Android versions — the simulator restarts advertising
automatically; just reconnect.

## Note

Real BLE verification against the official apps/CruxCoach requires a
machine with a Bluetooth adapter and must be done manually; the unit
tests cover protocol, geometry and registry but do not replace a device
test.
