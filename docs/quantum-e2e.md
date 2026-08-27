# Quantum Board simulator E2E

The simulator is an independent target for CruxCoach's Quantum scanner,
transport and renderer. It renders the five original model images bundled in
eWalls 2.0.14.

## Start and connect

```bash
sudo venv/bin/python main.py --board quantum --layout xl
```

CruxCoach should discover `QB_020000000001` (`QBB_…` for Belay), inspect GATT,
select `0000fff2-0000-1000-8000-00805f9b34fb`, subscribe to `fff1`, read state
at `fff4` and parse the 41-byte model identity from `fff5`, all below the
current `ffe0` service (`fff0` remains the app's legacy fallback). The
characteristic properties and payload formats come from the eWalls 2.0.14
application, not from a capture of physical Quantum hardware.

From a second BLE adapter or host:

```bash
venv/bin/pip install bleak
venv/bin/python tests/test_ble_mock_client.py quantum
```

The GUI uses model-specific public diode metadata (XL 657, L 549, M 432,
S Fitness 252, Belay 1264) on the corresponding original square image. Its
overlay transform is bytecode-recovered from eWalls 2.0.14 rather than inferred
from the pixels. Catalog types are XL=`big`, L=`medium`, M=`small`, S
Fitness=`xsmall`, Belay=`belay`.

## Automated contract

```bash
venv/bin/python -m pytest \
  tests/test_quantum_protocol.py \
  tests/test_quantum_geometry.py \
  tests/test_session.py \
  tests/test_multi_board.py -q
```

This covers byte-identical eWalls 2.0.14 vectors (raw UUID, CRC big-endian,
five-byte route-list request), every current command, one-byte fragmentation,
92-diode multi-chunk messages, CRC rejection/recovery, Modbus exceptions,
reconnect during a partial frame and isolation between sessions. Separate
regressions pin 1.44's CRC little-endian, removed commands and legacy JSON.

By default successful writes update the rendered LEDs and the simulated route
roster. The protocol returns a parser-compatible `fff1` event and an
authoritative `0x47` snapshot for `fff4`; the BLE layer publishes both. A route
uses eWalls' normal-play duration `0xffff`, which is statically recovered from
the application. This is a compatibility model for the app contract, not a
claim that every physical controller emits the same event or timing.

CRC/payload failures and an explicit reject fault profile produce Modbus
exception notifications without changing LED state. Those are deterministic
fault-injection contracts, not claims about malformed-frame firmware behaviour.

## Evidence boundary

No physical Quantum Board capture is currently available in this repository.
The Nokia bug reports used during development captured traffic to this
simulator. In particular, an ATT `Invalid Handle` in one report describes a
stale simulator/BlueZ handle after its GATT database changed; it is not evidence
about Quantum firmware. Exact controller response choice, latency, expiry and
reconnect behaviour therefore remain unverified hardware details.

The implemented compatibility contract is limited to facts recovered from
eWalls 2.0.14: command encodings, `0xffff` normal-play duration, the `fff1`
broadcast parser and the readable `fff4`/`fff5` roles. Modbus exceptions are
deterministic simulator fault injection, not a firmware-behaviour claim.

The original multiplex implementation called the selected session but dropped
its returned GATT updates. That made successful commands illuminate the GUI
while leaving `fff4` at `01 47 00 00` and `fff1` silent. Multiplex mode now
publishes the returned update on the selected board's characteristics.

## Two isolated boards

```bash
sudo venv/bin/python main.py --instances 2 \
  --board quantum --layout xl \
  --second-board quantum --second-layout s
```

Every advertising handle owns a separate session, frame accumulator, route
state and editor state. A partial frame sent to one slot cannot complete in or
mutate the other. `fff4` state and `fff5` identity reads are selected using
BlueZ's requesting-device path, so two Quantum instances remain isolated even
though their UUIDs are identical. BlueZ cannot target one characteristic
notification at only one subscribed central. When both instances share
`fff1`, the simulator suppresses that ambiguous notification rather than leak
one board's roster to its neighbour; each device can still read its own
authoritative `fff4` snapshot. A Quantum board paired with a different profile
has a unique `fff1` and receives normal notifications.

After updating a running simulator, stop the old process completely before
starting the new one so BlueZ unregisters its previous GATT database. Reconnect
the client and rediscover services; toggle client Bluetooth only if its OS kept
stale attribute handles.

## Geometry regeneration

Given the public Quantum SQLite snapshot:

```bash
python tools/build_quantum_geometry.py \
  /path/to/quantum.sqlite3 data/quantum_geometry.json
```

The output must contain only `address16`, `address32`, `kind`, `x` and `y`.
Never add routes, setters, profile paths, images, JWTs or account snapshots.

The five files under `assets/quantum/` are the byte-identical eWalls 2.0.14
`board-small` resources. They are not generated by the geometry tool. Do not
crop or re-encode them without revalidating the overlay transform.
