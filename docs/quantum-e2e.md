# Quantum Board simulator E2E

The simulator is an independent target for CruxCoach's Quantum scanner,
transport and renderer. It includes no Walltopia board-image asset.

## Start and connect

```bash
sudo venv/bin/python main.py --board quantum --layout xl
```

CruxCoach should discover `QB_020000000001` (`QBB_…` for Belay), inspect GATT, select
`0000fff2-0000-1000-8000-00805f9b34fb` for write-without-response and
subscribe to `fff1`, read state at `fff4` and parse the 41-byte model identity
from `fff5`, all below the current `ffe0` service (`fff0` remains the app's
legacy fallback).

From a second BLE adapter or host:

```bash
venv/bin/pip install bleak
venv/bin/python tests/test_ble_mock_client.py quantum
```

The GUI renders the 657-position authorised big fixture as a provisional
schematic for each model and changes panel proportions. It does not infer
model-specific address maps from marketing images. Catalog types are
XL=`big`, L=`medium`, M=`small`, S Fitness=`xsmall`, Belay=`belay`.

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

Successful 2.0.14 writes send the minimum state notification accepted by the
statically recovered `parseBroadcast`; fault profiles send exception frames.
`ProtocolEvent` remains an in-process diagnostic. Firmware timing, retries and
malformed-frame behaviour still require a real-controller capture.

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

Given an authorised local initial snapshot:

```bash
python tools/build_quantum_geometry.py \
  /path/to/big-initial.json data/quantum_geometry.json
```

The output must contain only `address16`, `address32`, `kind`, `x` and `y`.
Never add routes, setters, profile paths, images, JWTs or account snapshots.
