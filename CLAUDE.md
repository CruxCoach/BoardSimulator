# BoardSimulator — Consolidated BLE simulator for all CruxCoach boards

## Project Overview

Pure-software simulation of ALL seven interactive CruxCoach 0.2.0 boards
on Linux via BLE (BlueZ), in one codebase, selectable per CLI. One
process simulates exactly ONE board. The PC acts as a BLE peripheral;
the official apps and CruxCoach connect, write climb frames, and the
simulator decodes and visualizes them — Tkinter GUI or headless ASCII
grid.

Two protocol families behind one session abstraction:

- **aurora** — Kilter, Tension, Grasshopper, Decoy, So iLL, Touchstone.
  Binary packets, placement/LED model, SQLite geometry.
- **moonboard** — MoonBoard (4 variants). NUS-only GATT, ASCII frames,
  photo/coordinate-map rendering.

This repo SUPERSEDES the three sibling simulators (`KilterSimulator`,
`AuroraSimulator`, `MoonSimulator`) — those are read-only references
now. KilterSimulator's `get_board_details.py` / `led_position_parser.py`
are replaced by `--list` / `board_geometry.py`.

## Project Structure

```
BoardSimulator/
├── main.py               # CLI + preflight + BLE + renderer wiring
├── config.py             # Both families' protocol constants, name builders
├── boards.py             # Registry: 7 boards, AuroraVariant/MoonVariant
├── board_geometry.py     # LED->hole coords, edges, roles (Aurora SQLite)
├── board_state.py        # Shared base + AuroraBoardState/MoonBoardState
├── role_colors.py        # decoded RGB -> board-local role (per API level)
├── protocols/
│   ├── session.py        # GattProfiles + AuroraSession/MoonSession factory
│   ├── aurora_decoder.py # Aurora packet decoder (API level 2 + 3)
│   ├── aurora_encoder.py # Reference encoder (BoardPacketEncoder port) + frames
│   └── moonboard.py      # MoonBoard ASCII frame decoder + serpentine math
├── ble/
│   ├── peripheral.py     # BlueZ D-Bus peripheral + preflight_check()
│   ├── gatt.py           # GattProfile spec -> D-Bus GATT objects
│   └── advertising.py    # Extended-Advertising HCI helpers
├── render/               # aurora_gui/aurora_headless/moon_gui/moon_headless
├── data/<brand>.sqlite3  # Trimmed official board DBs (geometry tables only)
├── assets/<brand>/       # Board images (aurora) / photos + JSON maps (moon)
├── tools/                # build_data.py (5 aurora brands), trim_kilter_db.py
└── tests/                # 269-test pytest suite + manual BLE mock client
```

## Build / Run Commands

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# Run (live BLE needs root + adapter; --list needs neither)
sudo venv/bin/python main.py --board kilter
sudo venv/bin/python main.py --board kilter --layout homewall
sudo venv/bin/python main.py --board tension --layout tb2
sudo venv/bin/python main.py --board moonboard --layout mini-2020 --headless
python main.py --list

# Tests (no root, no adapter)
venv/bin/python -m pytest tests/ -q

# Manual BLE integration check (separate terminal, needs bleak)
venv/bin/python tests/test_ble_mock_client.py kilter
```

## Key Technical Details

### Session abstraction — THE seam

`protocols/session.py` is where the families plug into the shared BLE
peripheral. A session owns ble_name, GattProfile, decoder and state and
builds the family's renderer. Adding a board = registry entry (same
family) or a new Session class + GattProfile (new family); CLI and BLE
layers don't change. Aurora-only CLI options (--size/--api-level/
--serial) raise ValueError on the MoonBoard.

### Fail-fast BlueZ preflight (fixes a predecessor wart)

`ble.peripheral.preflight_check()` runs BEFORE the peripheral thread
starts: connects to the system bus, calls GetManagedObjects on
org.bluez, requires an org.bluez.Adapter1 at /org/bluez/hci0. Raises
`BlueZUnavailableError` with a human-readable reason → main exits 1.
A fatal error inside the peripheral thread triggers shutdown via the
on_fatal callback instead of leaving a dead thread behind a live GUI.
--list and the whole test suite never touch this path.

### dbus_fast gotcha

`ble/gatt.py` must NOT use `from __future__ import annotations` —
dbus_fast reads D-Bus signatures from live method annotations at
decoration time and breaks with postponed evaluation.

### Board-local roles & colours — THE gotcha

Role ids in climb frames (`p{placement}r{role}`) are board-local:
Kilter Original 12-15, Kilter Homewall 42-45, Aurora-family boards 1-4,
Tension Board 2 (product 5) 5-8. Colours come from each board's own
`placement_roles.led_color`; Kilter deviates from the Aurora-family
default (middle=cyan `00FFFF`, finish=magenta `FF00FF`, foot=orange
`FFA500`), So iLL too (middle=magenta, finish=white, foot=cyan).
`role_colors.RoleColorResolver` inverts the wire colour back to a role
per board and API level.

### Kilter specifics

Two layouts: original (layout 1, product 1, default size 10 = 12x12
with kickboard) and homewall (layout 8, product 7, default size 21 =
10x10) — ids mirror CruxCoach's `BoardConstants.KILTER_*`.
`leds_per_hold=2` (only board with 2; affects API-level-2 power budget).
Advertised name `Kilter Board#0001@3` parses under both scanner schemes;
CruxCoach treats kilter as the fallback brand for unmatched Aurora names.
Only size 10 has a board image (composited from the KilterSimulator's
bolt-ons + screw-ons layers); other sizes fall back to hold dots.

### MoonBoard specifics

NUS-only GATT (no discovery service), advertised name is the bare
"MoonBoard". ASCII frames `l#S0,P1,E197#`; serpentine strip arithmetic
is generalised over grid_rows (18 standard, 12 Mini 2020 — Mini wiring
is the BoardSesh-extrapolated assumption, real-hardware capture still
pending). The GUI has a live variant picker; the switch swaps the
decoder via `MoonSession.switch_variant` and clears the state.

### LED map semantics (aurora)

Decode side: `leds JOIN holes` per `product_size_id` (layout-independent,
like the physical controller). Encode side (tests/mock client):
`placements JOIN leds ON hole_id` filtered by layout + size — the same
join as CruxCoach's `getPlacementLedMap`.

### Decoder details

An empty Aurora ONLY packet (CruxCoach `encodeClear()`) fires
`on_message([])` so the board clears. API level 2 = 2 bytes/LED, 10-bit
position, 2-bit channels, 18 W power budget; keep @3 the default.
MoonBoard frames may arrive split across ≤20-byte writes; the parser
state machine mirrors BoardSesh's C++ (`~D` config = aux LEDs above
holds, skipped for finish holds).

## Data provenance

- `data/{tension,grasshopper,decoy,soill,touchstone}.sqlite3` + their
  assets: generated by `tools/build_data.py <source-root>` from a local
  RE workspace (official per-brand APK extracts). Copied verbatim from
  AuroraSimulator.
- `data/kilter.sqlite3`: `tools/trim_kilter_db.py <full-kilter-db>`
  (source: KilterSimulator's data/db.sqlite3), restricted to products
  1+7 / layouts 1+8. Kilter's older schema lacks `placements.set_id`;
  the trim derives it via `holds.set_id`.
- `assets/moonboard/`: photos + coordinate maps from MoonSimulator
  (FEAT-027 vector pipeline).
- Test fixture values (LED counts, placement->LED samples, role tables,
  edges) were verified against those DBs and CruxCoach 0.2.0's
  BoardConstants. Do NOT commit absolute paths to any workspace.

## Conventions

- Code + comments English; README German (sibling-project style).
- Single responsibility per module; max ~500 lines per file; thread
  safety via `threading.Lock`.
- Type hints throughout; Python 3.10+.
- Conventional commits; never push (local-only repo).
- Unit tests must run without root/adapter; real-device BLE verification
  is manual and must not be claimed as tested.
