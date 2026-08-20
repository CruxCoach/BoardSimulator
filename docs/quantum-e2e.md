# Quantum Board simulator E2E

The simulator is an independent target for CruxCoach's Quantum scanner,
transport and renderer. It includes no Walltopia board-image asset.

## Start and connect

```bash
sudo venv/bin/python main.py --board quantum --layout xl
```

CruxCoach should discover `QuantumXL_020000000001`, inspect GATT, select
`0000fff2-0000-1000-8000-00805f9b34fb` for write-without-response and
optionally subscribe to `fff1`. Real boards may use another service UUID, so
match characteristic suffixes rather than the simulator's `fff0` service.

From a second BLE adapter or host:

```bash
venv/bin/pip install bleak
venv/bin/python tests/test_ble_mock_client.py quantum
```

The GUI renders 657 diode positions for XL/L/M/Belay and the 432 independently
observed small positions for S. The model changes panel proportions. It does
not infer model-specific address maps from marketing images.

## Automated contract

```bash
venv/bin/python -m pytest \
  tests/test_quantum_protocol.py \
  tests/test_quantum_geometry.py \
  tests/test_session.py \
  tests/test_multi_board.py -q
```

This covers byte-identical ewalls 1.44 golden vectors, every command from
`0x41` through `0x48` and `0x64` through `0x67`, one-byte fragmentation,
multi-chunk messages, CRC rejection and recovery, injected command rejection,
reconnect during a partial frame, swipe/off/change/editor semantics, legacy
JSON and isolation between two Quantum sessions.

`ProtocolEvent` acknowledgements are simulator diagnostics. They are not sent
as invented firmware notifications: ewalls 1.44 only logs `fff1` as UTF-8 and
a real-controller response capture is still required for wire parity.

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
