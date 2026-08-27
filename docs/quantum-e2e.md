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
current `ffe0` service (`fff0` remains the app's legacy fallback). The captured
XL declared fff2 with the write-without-response property, while the Nokia
actually sent ATT Write Requests and the controller returned ATT Write
Responses. The simulator keeps the captured declaration; the mock client uses
requests to exercise the observed Android path.

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

By default successful writes change the rendered LEDs but send no fff1 value
and do not rewrite the initial empty fff4 snapshot. This is intentional: it is
the behaviour observed below, and ATT write success is not a route-roster
confirmation. `ProtocolEvent` remains an in-process diagnostic.

Parser-compatible route snapshots are still available for isolated app tests,
but are explicitly synthetic rather than the default hardware personality:

```python
QuantumProtocol(
    on_action,
    response_policy=ResponsePolicy.SYNTHETIC_STATE,
)
```

CRC/payload failures and an explicit reject fault profile produce Modbus
exception notifications without changing LED state. Those are deterministic
fault-injection contracts, not claims about malformed-frame firmware behaviour.

## Real XL evidence (2026-08-27)

Two Nokia/Quantum-XL btsnooz captures were compared with eWalls 2.0.14's
decompiled encoder and broadcast parser:

| Event | Captured controller result |
|---|---|
| `TURN_OFF_USER` (`0x43`) | ATT Write Response; no fff1 notification |
| `ACTIVATE_WALL` (`0x41`, 300 seconds) | ATT Write Response and physical LEDs on; fff4 remained `01 47 00 00` |
| `ACTIVATE_WALL` (`0x41`, app default `0xffff`) | ATT Write Response and physical LEDs on; fff4 still remained `01 47 00 00` |
| `REQUEST_ROUTE_LIST` (`0x47`) | normally ATT Write Response, then explicit fff4 read returned `01 47 00 00`; no fff1 notification |

The btsnooz recorder truncates long ACL payloads, so the captured `0x41` frame
does not itself retain its duration bytes. `0xffff` is pinned independently by
the exact CruxCoach build used for the second run and by eWalls 2.0.14's normal
route-play generator. It is therefore an app encoder fact, not a general
controller rule: both durations lit this XL and neither entered its roster.

One later `0x47` request explains Android's reported GATT status `0x1`
precisely. The phone sent an ATT Write Request to the formerly valid fff2 value
handle `0x0146`; the controller returned `01 12 46 01 01`, an ATT Error
Response for that request with `Invalid Handle`. After reconnect and service
rediscovery, fff2 had moved to `0x0166` and fff4 to `0x016b`; requests succeeded
again, but the roster remained empty. Thus the physical activation succeeded
before the authoritative refresh failed.

BlueZ assigns the simulator's attribute handles, so this dynamic real-device
handle-generation/cache failure cannot be reproduced safely inside the
protocol decoder. Reconnect is covered at the level the simulator controls:
partial transport frames are discarded, rendered controller LED state is
preserved, and the captured-XL fff4 snapshot stays empty. Client recovery from
an ATT `Invalid Handle` needs a real GATT fault peripheral or hardware test.

Observed turnaround was roughly 49 ms for the off write, 110 ms for the
activation write, 135 ms for a route-list write and 138 ms for the following
fff4 read in the representative run. These are measurements, not stable
firmware timers, so the simulator does not bake in sleeps or retry constants.

## Two isolated boards

```bash
sudo venv/bin/python main.py --instances 2 \
  --board quantum --layout xl \
  --second-board quantum --second-layout s
```

Every advertising handle owns a separate session, frame accumulator, route
state and editor state. A partial frame sent to one slot cannot complete in or
mutate the other.

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
