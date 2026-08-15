"""Advertising-data builders + Extended Advertising via hcitool.

BlueZ's own LE Set Extended Advertising Parameters command may FAIL
(Command Disallowed 0x0C) because advertising is already active when
BlueZ tries to set the parameters. This means the legacy PDU flag
(ADV_IND) is never set, and the controller may use non-legacy Extended
Advertising PDUs that phone apps don't scan for.

Solution: after BlueZ has started advertising (Adapter.Discoverable),
stop advertising -> set parameters (with legacy PDU) -> set data ->
set scan response -> restart advertising — all via raw HCI commands.

Every command is pinned to ONE controller with ``-i hciN``: an unpinned
hcitool talks to the first adapter it finds, so on a two-adapter host
the second simulator would happily overwrite the first board's
advertising set and take its identity over.
"""

from __future__ import annotations

import logging
import subprocess

from ble.adapter import DEFAULT_ADAPTER, hci_args

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Advertising data helpers
# ---------------------------------------------------------------------------

def _uuid_to_le_bytes(uuid_str: str) -> bytes:
    """Convert a UUID string to little-endian bytes for BLE advertising."""
    hex_str = uuid_str.replace("-", "")
    big_endian = bytes.fromhex(hex_str)
    return big_endian[::-1]  # reverse for little-endian


def build_adv_data(uuid_str: str) -> bytes:
    """Build advertising data containing Flags + 128-bit Service UUID.

    AD Structure 1: Flags (3 bytes)
      02 01 06  (LE General Discoverable + BR/EDR Not Supported)

    AD Structure 2: Complete List of 128-bit Service UUIDs (18 bytes)
      11 07 <uuid_le>

    Total: 21 bytes (fits in 31-byte limit, also fits in 251-byte
    extended limit).
    """
    flags = bytes([0x02, 0x01, 0x06])
    uuid_bytes = _uuid_to_le_bytes(uuid_str)
    uuid_ad = bytes([0x11, 0x07]) + uuid_bytes
    return flags + uuid_ad


def build_scan_response(name: str) -> bytes:
    """Build scan response data containing the Complete Local Name."""
    name_bytes = name.encode("utf-8")
    return bytes([len(name_bytes) + 1, 0x09]) + name_bytes


# ---------------------------------------------------------------------------
# HCI status parsing
# ---------------------------------------------------------------------------

def _parse_hci_status(hcitool_output: str) -> int | None:
    """Parse the HCI status byte from hcitool Command Complete event output."""
    lines = hcitool_output.split("\n")
    in_event = False
    for line in lines:
        if "HCI Event: 0x0e" in line:
            in_event = True
            continue
        if in_event and line.strip():
            parts = line.strip().split()
            try:
                if len(parts) >= 4:
                    return int(parts[3], 16)
            except (ValueError, IndexError):
                pass
            break
    return None


def _hci_status_name(status: int) -> str:
    """Return a human-readable name for common HCI status codes."""
    names = {
        0x00: "Success",
        0x01: "Unknown HCI Command",
        0x02: "Unknown Connection Identifier",
        0x0C: "Command Disallowed",
        0x11: "Unsupported Feature",
        0x12: "Invalid HCI Command Parameters",
        0x42: "Unknown Advertising Identifier",
    }
    return names.get(status, f"Unknown (0x{status:02x})")


# ---------------------------------------------------------------------------
# Extended Advertising via hcitool
# ---------------------------------------------------------------------------

def _hcitool_cmd(label: str, ogf_ocf: str, params: list[str],
                 adapter: str = DEFAULT_ADAPTER) -> bool:
    """Send a single hcitool command to one adapter and check HCI status."""
    cmd = ["hcitool"] + hci_args(adapter) + ["cmd"] + ogf_ocf.split() + params

    logger.debug("hcitool %s: %s", label, " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            logger.warning("[%s] hcitool %s failed (rc=%d): %s",
                           adapter, label, result.returncode,
                           result.stderr.strip())
            return False

        output = result.stdout.strip()
        hci_status = _parse_hci_status(output)
        if hci_status is not None and hci_status != 0:
            logger.warning("[%s] hcitool %s: HCI status 0x%02x (%s)",
                           adapter, label, hci_status,
                           _hci_status_name(hci_status))
            if output:
                logger.debug("hcitool output: %s", output)
            return False

        logger.info("[%s] hcitool %s: OK", adapter, label)
        if output:
            logger.debug("hcitool output: %s", output)
        return True

    except FileNotFoundError:
        logger.warning("hcitool not found")
        return False
    except subprocess.TimeoutExpired:
        logger.warning("[%s] hcitool %s timed out", adapter, label)
        return False


def disable_extended_adv(adapter: str = DEFAULT_ADAPTER) -> bool:
    """Stop advertising on handle 0x00 of ONE adapter.

    Used to emulate an exclusive controller: a peripheral can only be
    connected to WHILE it advertises, so switching off the advertisement is
    what makes a board look "one client at a time" to everyone else. The
    adapter argument keeps that silence local to its own realm.
    """
    return _hcitool_cmd("ext adv disable", "0x08 0x0039", [
        "00",              # Enable: disabled
        "01",              # Number_of_Sets: 1
        "00",              # Handle: 0x00
        "00", "00",        # Duration: 0 (no limit)
        "00",              # Max_Events: 0 (no limit)
    ], adapter=adapter)


def set_extended_adv_data(uuid_str: str, name: str,
                          adapter: str = DEFAULT_ADAPTER) -> bool:
    """Set up Extended Advertising with legacy PDU on handle 0x00.

    Sequence:
      1. LE Set Extended Advertising Enable (0x08|0x0039) -> disable
      2. LE Set Extended Advertising Parameters (0x08|0x0036) -> legacy ADV_IND
      3. LE Set Extended Advertising Data (0x08|0x0037) -> Flags + UUID
      4. LE Set Extended Scan Response Data (0x08|0x0038) -> device name
      5. LE Set Extended Advertising Enable (0x08|0x0039) -> enable
    """
    adv_data = build_adv_data(uuid_str)
    scan_rsp = build_scan_response(name)

    logger.info("[%s] Configuring Extended Advertising with legacy PDU "
                "(handle 0x00)", adapter)
    logger.info("  Adv data (%d bytes): %s", len(adv_data), adv_data.hex(" "))
    logger.info("  Scan rsp (%d bytes): %s", len(scan_rsp), scan_rsp.hex(" "))

    # Step 1: Disable advertising on handle 0x00
    disable_ok = _hcitool_cmd("ext adv disable", "0x08 0x0039", [
        "00",              # Enable: disabled
        "01",              # Number_of_Sets: 1
        "00",              # Handle: 0x00
        "00", "00",        # Duration: 0 (no limit)
        "00",              # Max_Events: 0 (no limit)
    ], adapter=adapter)
    if not disable_ok:
        logger.warning("Could not disable advertising, trying data-only approach")
        return _set_extended_adv_data_only(adv_data, scan_rsp, adapter=adapter)

    # Step 2: Set advertising parameters with legacy PDU flag
    params_ok = _hcitool_cmd("ext adv params", "0x08 0x0036", [
        "00",              # Advertising_Handle: 0x00
        "13", "00",        # Properties: Connectable+Scannable+Legacy (ADV_IND)
        "a0", "00", "00",  # Min interval: 0x00A0 = 160 * 0.625ms = 100ms
        "00", "01", "00",  # Max interval: 0x0100 = 256 * 0.625ms = 160ms
        "07",              # Channel map: 37, 38, 39 (all)
        "00",              # Own address type: Public
        "00",              # Peer address type: Public
        "00", "00", "00", "00", "00", "00",  # Peer address: 00:00:00:00:00:00
        "00",              # Filter policy: Allow all
        "7f",              # TX power: Host has no preference
        "01",              # Primary PHY: LE 1M
        "00",              # Secondary max skip: 0
        "01",              # Secondary PHY: LE 1M
        "00",              # Advertising SID: 0
        "00",              # Scan request notification: Disabled
    ], adapter=adapter)
    if not params_ok:
        logger.warning("Could not set advertising parameters")

    # Step 3: Set advertising data (Flags + UUID)
    adv_ok = _hcitool_cmd("ext adv data", "0x08 0x0037", [
        "00",                                          # Handle: 0x00
        "03",                                          # Operation: Complete
        "01",                                          # Fragment pref: minimize
        f"{len(adv_data):02x}",                       # Data length
    ] + [f"{b:02x}" for b in adv_data], adapter=adapter)

    # Step 4: Set scan response data (device name)
    scan_ok = _hcitool_cmd("ext scan rsp", "0x08 0x0038", [
        "00",                                          # Handle: 0x00
        "03",                                          # Operation: Complete
        "01",                                          # Fragment pref: minimize
        f"{len(scan_rsp):02x}",                       # Data length
    ] + [f"{b:02x}" for b in scan_rsp], adapter=adapter)

    # Step 5: Re-enable advertising on handle 0x00
    enable_ok = _hcitool_cmd("ext adv enable", "0x08 0x0039", [
        "01",              # Enable: enabled
        "01",              # Number_of_Sets: 1
        "00",              # Handle: 0x00
        "00", "00",        # Duration: 0 (no limit)
        "00",              # Max_Events: 0 (no limit)
    ], adapter=adapter)

    if params_ok and adv_ok and scan_ok and enable_ok:
        logger.info("[%s] Full Extended Advertising setup complete "
                    "(legacy ADV_IND)", adapter)
        return True

    if enable_ok and adv_ok:
        logger.warning("Advertising active with data, but some steps failed")
        return True

    return False


def _set_extended_adv_data_only(adv_data: bytes, scan_rsp: bytes,
                                adapter: str = DEFAULT_ADAPTER) -> bool:
    """Fallback: overwrite advertising data without stopping/restarting.

    Used when we can't disable advertising (e.g. no active handle).
    """
    logger.info("[%s] Fallback: overwriting advertising data only "
                "(no param change)", adapter)

    adv_ok = _hcitool_cmd("ext adv data", "0x08 0x0037", [
        "00", "03", "01", f"{len(adv_data):02x}",
    ] + [f"{b:02x}" for b in adv_data], adapter=adapter)

    scan_ok = _hcitool_cmd("ext scan rsp", "0x08 0x0038", [
        "00", "03", "01", f"{len(scan_rsp):02x}",
    ] + [f"{b:02x}" for b in scan_rsp], adapter=adapter)

    return adv_ok and scan_ok
