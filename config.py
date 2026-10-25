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
  independently selected XL/L/M/S/Belay image.
- Shiny Wall: one ESP32-style write characteristic, ASCII state vectors
  ``.c0+c1+…+cN.`` (one role code per physical hold), ``-`` clears.
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


# Aurora controller generations. The classic Aurora box advertises
# 'Name#serial@apiLevel' and takes writes with and without response.
# Kilter-built boxes (shipped since mid-2025) advertise only the bare
# product name ("Kilter Board"), speak API level 2 and take
# write-with-response only; the Kilter app writes them in 20-byte chunks
# 100 ms apart (Boardsesh field telemetry: RX properties = 8).
CONTROLLER_AURORA: str = "aurora"
CONTROLLER_KILTER_BUILT: str = "kilter-built"
CONTROLLERS: tuple[str, ...] = (CONTROLLER_AURORA, CONTROLLER_KILTER_BUILT)
KILTER_BUILT_API_LEVEL: int = 2
KILTER_APP_CHUNK_GAP_MS: int = 100


def kilter_built_ble_name(display_name: str) -> str:
    """The bare name a Kilter-built box advertises: no serial, no API level.

    Apps that only parse 'Name#serial@apiLevel' never list such a box;
    the official app finds it by the advertised Aurora service.
    """
    return display_name


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


# ── Shiny Wall ───────────────────────────────────────────────────────────

# The controller is an ESP32. Its write characteristic is the one from the
# ESP32 Arduino BLE server example; the official app looks it up across all
# services, so the service UUID is not part of the contract. The simulator
# uses the example's service UUID by default (--service-uuid overrides it).
SHINYWALL_SERVICE_UUID: str = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
SHINYWALL_WRITE_UUID: str = "beb5483e-36e1-4688-b7f5-ea07361b26a8"
SHINYWALL_DEFAULT_IDENTITY: str = "0001"
# The Mini advertises one fixed name instead of ShinyWall_<code>_<id>.
SHINYWALL_MINI_NAME: str = "Shiny Wall ST0 (ShinyMini)"
# Characteristic properties the simulator offers by default: the example's
# read + write, write-without-response so either write type lands, and
# notify, which the official app subscribes to when it waits for an "OK"
# (traverses); the "OK" itself is only sent with --shiny-ack.
SHINYWALL_DEFAULT_PROPS: tuple[str, ...] = (
    "read", "write", "write-without-response", "notify")
SHINYWALL_ALLOWED_PROPS: tuple[str, ...] = (
    "read", "write", "write-without-response", "notify", "indicate")

# A legacy scan response holds 31 bytes; the Complete Local Name AD
# structure spends 2 of them on its header.
SCAN_RESPONSE_NAME_LIMIT: int = 29


def check_ble_name(name: str) -> str:
    """Return ``name`` or raise ValueError when it cannot be advertised.

    The name travels in a legacy scan response, so more than 29 UTF-8
    bytes cannot be sent at all; an empty name would not be found.
    """
    size = len(name.encode("utf-8"))
    if not name:
        raise ValueError("the BLE name must not be empty")
    if size > SCAN_RESPONSE_NAME_LIMIT:
        raise ValueError(
            f"BLE name {name!r} is {size} bytes; a scan response carries at "
            f"most {SCAN_RESPONSE_NAME_LIMIT}")
    return name


def shinywall_ble_name(name_code: str | None,
                       identity: str = SHINYWALL_DEFAULT_IDENTITY) -> str:
    """Name a Shiny Wall controller advertises: ``ShinyWall_<code>_<id>``.

    ``name_code`` is the variant's code (``STP``, ``STF``, ``STM``,
    ``STMF``); ``None`` means the Mini, which advertises a fixed name.
    """
    if name_code is None:
        return SHINYWALL_MINI_NAME
    return check_ble_name(f"ShinyWall_{name_code}_{identity}")


def normalize_uuid(value: str) -> str:
    """Canonical lower-case 128-bit UUID string, or ValueError."""
    import uuid

    try:
        return str(uuid.UUID(value.strip()))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"not a UUID: {value!r}") from exc


# ── Runtime ──────────────────────────────────────────────────────────────

def headless_mode() -> bool:
    """Whether the simulator should run without the Tkinter GUI.

    Enabled via the ``BOARDSIM_HEADLESS`` environment variable (any
    non-empty value other than ``0`` / ``false``) or the ``--headless``
    CLI flag handled in main.py.
    """
    value = os.environ.get("BOARDSIM_HEADLESS", "").strip().lower()
    return value not in ("", "0", "false", "no")
