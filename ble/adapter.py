"""Which Bluetooth controller one simulator instance drives.

A process still simulates exactly ONE board, but a host with several
controllers can run several of them side by side: one board per adapter,
each its own BLE realm with its own advertised identity, its own GATT
database and its own centrals. Nothing is shared between two such
processes except the machine.

Every per-adapter detail is derived from the name held here — the BlueZ
object path (``/org/bluez/hci1``), the device-path prefix used to count
centrals, the ``path_namespace`` of the D-Bus signal subscription and
the ``-i hci1`` flag every ``hcitool`` call needs. Deriving them in one
place is what keeps a second realm from reacting to the first one's
connects or silencing its advertisement.

Names are validated on the way in, because that string ends up in a
D-Bus object path, in a match rule and in a subprocess argv.
"""

from __future__ import annotations

import re

#: BlueZ numbers its controllers hci0, hci1, … — the first one is the
#: default, so single-adapter hosts never have to think about this.
DEFAULT_ADAPTER = "hci0"

BLUEZ_ROOT_PATH = "/org/bluez"

# Exactly one spelling per controller: 'hci' + a decimal index with no
# leading zeros. Anything looser would let a '/', a '..' or a quote into
# an object path, a match rule and a command line.
_ADAPTER_RE = re.compile(r"hci(?:0|[1-9][0-9]*)")


class InvalidAdapterError(ValueError):
    """The given name is not a BlueZ controller name (``hciN``)."""


def normalize_adapter(adapter: str | None = None) -> str:
    """Return the canonical controller name, or raise.

    ``None`` means "the default adapter", so callers that never learned
    about multi-adapter operation keep working unchanged.
    """
    if adapter is None:
        return DEFAULT_ADAPTER
    if not isinstance(adapter, str):
        raise InvalidAdapterError(
            f"adapter name must be a string, got {type(adapter).__name__}")
    name = adapter.strip().lower()
    if not _ADAPTER_RE.fullmatch(name):
        raise InvalidAdapterError(
            f"{adapter!r} is not a Bluetooth adapter name — expected 'hciN', "
            f"e.g. {DEFAULT_ADAPTER!r} or 'hci1'; run 'hciconfig' to see the "
            "controllers on this host"
        )
    return name


def adapter_path(adapter: str | None = None) -> str:
    """BlueZ object path of the adapter, e.g. ``/org/bluez/hci1``."""
    return f"{BLUEZ_ROOT_PATH}/{normalize_adapter(adapter)}"


def device_path_prefix(adapter: str | None = None) -> str:
    """Path prefix shared by every Device1 BlueZ creates on this adapter.

    Centrals of another realm live under another adapter's path, so this
    prefix is what makes the connection count per-realm.
    """
    return f"{adapter_path(adapter)}/dev_"


def hci_args(adapter: str | None = None) -> list[str]:
    """``['-i', 'hci1']`` — pins one ``hcitool`` call to one controller.

    Without it hcitool picks the first available controller, which on a
    two-adapter host means one realm reprogramming the other's
    advertising set.
    """
    return ["-i", normalize_adapter(adapter)]


def device_signal_match_rule(adapter: str | None = None) -> str:
    """Match rule for Device1 property changes on THIS adapter only.

    The ``path_namespace`` is what keeps the other realm's connect and
    disconnect signals out of this process — both simulators subscribe
    to the same bus and the same interface.
    """
    return (
        "type='signal',"
        "sender='org.bluez',"
        "interface='org.freedesktop.DBus.Properties',"
        "member='PropertiesChanged',"
        f"path_namespace='{adapter_path(adapter)}'"
    )
