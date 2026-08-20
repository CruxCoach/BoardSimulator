"""Central configuration for the consolidated board simulator.

Board identity (which board is simulated, which layout and
product size) is selected at runtime via the CLI — see main.py and the
registry in boards.py. This module holds the protocol-level constants of
both BLE protocol families and the runtime defaults.

Three protocol families:

- Aurora (Kilter, Tension, Grasshopper, Decoy, So iLL, Touchstone):
  empty discovery service + Nordic UART Service, binary packets
  ``[0x01][len][checksum][0x02][type][holds…][0x03]``, advertised name
  ``Name#serial@apiLevel``.
- MoonBoard: Nordic UART Service only (advertised itself), plain-ASCII
  frames ``l#<token><pos>,…#``, advertised name is the bare "MoonBoard".
- Quantum: characteristic-based fff2 writes, CRC16/MODBUS frames and an
  independently selected XL/L/M/S/Belay schematic.
"""

import os

# ── Aurora family ────────────────────────────────────────────────────────

# Default board serial number, advertised as the '#serial' name suffix.
# The official apps parse it with the regex '#[a-z0-9]+' (case-insensitive);
# CruxCoach takes everything between '#' and '@'.
BOARD_SERIAL: str = "0001"

# Aurora protocol API level, advertised as the '@N' name suffix.
# 3 = modern boards (3 bytes/hold); 2 = legacy hardware (2 bytes/hold with
# an 18 W power-budget brightness scale). Scanners default to 2 when the
# suffix is missing, so the suffix is always advertised.
API_LEVEL: int = 3

# BLE UUIDs — identical across the whole Aurora-Climbing ecosystem
# (RE-verified in every brand app's BluetoothServiceKt).
AURORA_ADVERTISING_SERVICE_UUID: str = "4488b571-7806-4df6-bcff-a2897e4953ff"
UART_SERVICE_UUID: str = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
RX_CHARACTERISTIC_UUID: str = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
TX_CHARACTERISTIC_UUID: str = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

# Aurora boards chunk GATT writes to 20 bytes (bluetoothChunkSize).
BLE_CHUNK_SIZE: int = 20


def aurora_ble_name(display_name: str, serial: str = BOARD_SERIAL,
                    api_level: int = API_LEVEL) -> str:
    """Build the Aurora BLE advertising name: 'Name#serial@apiLevel'.

    Must contain the brand's official-app filter substring (e.g. 'Tension',
    'Kilter') and parse under CruxCoach's 'Name#serial@apiLevel' scheme.
    Keep it short enough for the 31-byte scan-response budget (name AD
    header = 2 bytes).
    """
    return f"{display_name}#{serial}@{api_level}"


# ── MoonBoard ────────────────────────────────────────────────────────────

# MoonBoard scanners filter on the name prefix "MoonBoard" (no Aurora
# '#serial@apiLevel' suffix). The advertised name MUST start with
# "MoonBoard" or the official app / CruxCoach will not list the device.
MOONBOARD_BLE_NAME: str = "MoonBoard"

# Serpentine-wired LED strip — the controller addresses 200 LEDs even
# though only 198 carry holds (see protocols/moonboard.py).
MOON_STRIP_LED_COUNT: int = 200

# eWalls 2.0.14 prefers ffe0 and retains fff0 as a legacy service fallback.
QUANTUM_SERVICE_UUID: str = "0000ffe0-0000-1000-8000-00805f9b34fb"
QUANTUM_LEGACY_SERVICE_UUID: str = "0000fff0-0000-1000-8000-00805f9b34fb"
QUANTUM_NOTIFY_UUID: str = "0000fff1-0000-1000-8000-00805f9b34fb"
QUANTUM_WRITE_UUID: str = "0000fff2-0000-1000-8000-00805f9b34fb"
QUANTUM_STATE_UUID: str = "0000fff4-0000-1000-8000-00805f9b34fb"
QUANTUM_CONFIG_UUID: str = "0000fff5-0000-1000-8000-00805f9b34fb"


def quantum_ble_name(model_key: str, identity: str = "020000000001") -> str:
    """Build the current eWalls scanner name; fff5 identifies the model."""
    prefix = "QBB" if model_key.lower() == "belay" else "QB"
    return f"{prefix}_{identity}"


# ── Runtime ──────────────────────────────────────────────────────────────

def headless_mode() -> bool:
    """Whether the simulator should run without the Tkinter GUI.

    Enabled via the ``BOARDSIM_HEADLESS`` environment variable (any
    non-empty value other than ``0`` / ``false``) or the ``--headless``
    CLI flag handled in main.py.
    """
    value = os.environ.get("BOARDSIM_HEADLESS", "").strip().lower()
    return value not in ("", "0", "false", "no")
