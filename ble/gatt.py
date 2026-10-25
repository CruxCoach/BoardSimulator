"""GATT layer: profile description + BlueZ D-Bus service objects.

A :class:`GattProfile` describes one protocol family's GATT shape
declaratively; the peripheral turns it into the D-Bus objects BlueZ
reads through ``GetManagedObjects``:

- Aurora boards expose TWO services: an empty discovery service
  (``4488B571-…``, the UUID the official apps historically filter on)
  and the Nordic UART Service whose RX characteristic receives the
  climb packets.
- A MoonBoard exposes ONLY the Nordic UART Service (and advertises that
  service's UUID itself) with a write RX characteristic and a
  notify-only TX stub.
"""

# NOTE: no `from __future__ import annotations` here — dbus_fast reads
# the D-Bus signatures from live method annotations at decoration time.
import contextvars
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from dbus_fast import Variant
from dbus_fast.service import ServiceInterface, method, dbus_property, PropertyAccess

logger = logging.getLogger(__name__)

APP_BASE_PATH = "/org/boardsim"


# ---------------------------------------------------------------------------
# Declarative profile description
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CharacteristicSpec:
    """One GATT characteristic: UUID + BlueZ flags.

    ``receives_writes`` marks the characteristic whose WriteValue calls
    are forwarded to the protocol decoder (the UART RX characteristic).
    """

    uuid: str
    flags: tuple[str, ...]
    receives_writes: bool = False
    initial_value: bytes = b""
    # Pacing a controller expects between writes (Kilter-built box: the
    # Kilter app's 100 ms); 0 = no check. See :class:`WriteGuard`.
    min_write_gap_ms: int = 0
    # Drop a write that arrives sooner, like a parser that cannot keep up.
    drop_unpaced: bool = False


@dataclass(frozen=True)
class ServiceSpec:
    """One GATT service with its characteristics."""

    uuid: str
    characteristics: tuple[CharacteristicSpec, ...] = ()


@dataclass(frozen=True)
class GattProfile:
    """The complete GATT + advertising shape of one protocol family."""

    description: str       # for logs ("discovery + UART service", …)
    advertised_uuid: str   # the service UUID injected into advertising data
    services: tuple[ServiceSpec, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class GattUpdate:
    """One protocol result with independent notify and readable state data.

    Quantum controllers expose asynchronous events on fff1 and an
    authoritative snapshot on fff4. Keeping those channels separate prevents
    a user-off event from replacing the readable route-list snapshot.
    """

    notification: bytes | None = None
    read_value: bytes | None = None


class WriteGuard:
    """Checks the writes one characteristic receives against its controller.

    - A write without response ("command") to a characteristic without the
      write-without-response flag is reported once per central: Android
      delivers it, iOS drops it on the sending side, so an app that relies
      on it lights nothing on iOS.
    - With ``min_gap_ms`` set, a write arriving sooner than that after the
      previous accepted one of the same burst is reported, or dropped when
      ``drop_unpaced`` is set (the box is still busy with the last chunk).
      BLE connection intervals jitter arrivals, so 75 % of the gap counts.
    """

    BURST_MS = 1000.0
    TOLERANCE = 0.75

    def __init__(self, flags, min_gap_ms: int = 0, drop_unpaced: bool = False,
                 clock: Callable[[], float] = time.monotonic):
        self.accepts_commands = "write-without-response" in flags
        self.min_gap_ms = min_gap_ms
        self.drop_unpaced = drop_unpaced
        self._clock = clock
        self._last_accepted: dict[str | None, float] = {}
        self._warned_burst: set[str | None] = set()
        self._warned_commands: set[str | None] = set()
        self.unpaced = 0
        self.dropped = 0

    def accept(self, length: int, write_type: str | None, peer: str | None) -> bool:
        who = peer or "a central"
        if (write_type == "command" and not self.accepts_commands
                and peer not in self._warned_commands):
            self._warned_commands.add(peer)
            logger.warning(
                "%s writes without response to a write-only characteristic; "
                "iOS drops such writes, the Kilter app always writes with response",
                who)
        if self.min_gap_ms <= 0:
            return True
        now = self._clock()
        last = self._last_accepted.get(peer)
        gap_ms = None if last is None else (now - last) * 1000.0
        if gap_ms is None or gap_ms >= self.BURST_MS:
            self._warned_burst.discard(peer)
        elif gap_ms < self.min_gap_ms * self.TOLERANCE:
            self.unpaced += 1
            if self.drop_unpaced:
                self.dropped += 1
                logger.warning(
                    "dropped a %d-byte write %.0f ms after the previous one "
                    "(strict pacing, %d ms expected)", length, gap_ms, self.min_gap_ms)
                return False
            if peer not in self._warned_burst:
                self._warned_burst.add(peer)
                logger.warning(
                    "%s sends chunks %.0f ms apart; this controller expects %d ms "
                    "(the Kilter app's pacing)", who, gap_ms, self.min_gap_ms)
        self._last_accepted[peer] = now
        return True


def _option(options, key: str):
    raw = options.get(key)
    return getattr(raw, "value", raw)


@dataclass(frozen=True)
class WriteInfo:
    """What BlueZ tells about one GATT write besides its bytes.

    ``write_type`` is ``request`` (with response), ``command`` (without
    response) or ``reliable`` (prepared/long write); ``mtu`` is the exchanged
    ATT MTU (BlueZ 5.62+), ``peer`` the D-Bus device path. Each is ``None``
    when BlueZ does not send it.
    """

    write_type: str | None = None
    mtu: int | None = None
    offset: int = 0
    peer: str | None = None

    @property
    def address(self) -> str | None:
        """``AA:BB:CC:DD:EE:FF`` from a ``…/dev_AA_BB_…`` device path."""
        if not self.peer:
            return None
        tail = self.peer.rsplit("/", 1)[-1]
        return tail[4:].replace("_", ":") if tail.startswith("dev_") else tail


# Set around the write callback, so a session can read the write's context
# without changing the on_data(bytes) signature every family shares.
CURRENT_WRITE: contextvars.ContextVar[WriteInfo | None] = contextvars.ContextVar(
    "boardsim_current_write", default=None)


def current_write() -> WriteInfo | None:
    """Context of the GATT write being handled right now, if any."""
    return CURRENT_WRITE.get()


def _write_info(options, peer: str | None) -> WriteInfo:
    mtu = _option(options, "mtu")
    offset = _option(options, "offset")
    write_type = _option(options, "type")
    return WriteInfo(
        write_type=str(write_type) if write_type else None,
        mtu=int(mtu) if mtu else None,
        offset=int(offset) if offset else 0,
        peer=peer)


# ---------------------------------------------------------------------------
# D-Bus GATT objects (BlueZ reads these through GetManagedObjects)
# ---------------------------------------------------------------------------

class GattCharacteristic(ServiceInterface):
    """A single GATT characteristic exposed over D-Bus."""

    def __init__(self, index: int, uuid: str, flags: list[str],
                 service_path: str, initial_value: bytes = b"",
                 min_write_gap_ms: int = 0, drop_unpaced: bool = False):
        self.path = f"{service_path}/char{index}"
        self.write_guard = WriteGuard(flags, min_write_gap_ms, drop_unpaced)
        self._uuid = uuid
        self._flags = flags
        self._service_path = service_path
        self._value = bytearray(initial_value)
        self._notifying = False
        self.write_callback: Callable[[bytes, str | None], None] | None = None
        self.read_callback: Callable[[str | None], bytes] | None = None
        super().__init__("org.bluez.GattCharacteristic1")

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":  # type: ignore  # noqa: N802
        return self._uuid

    @dbus_property(access=PropertyAccess.READ)
    def Service(self) -> "o":  # type: ignore  # noqa: N802
        return self._service_path

    @dbus_property(access=PropertyAccess.READ)
    def Flags(self) -> "as":  # type: ignore  # noqa: N802
        return self._flags

    @dbus_property(access=PropertyAccess.READ)
    def Value(self) -> "ay":  # type: ignore  # noqa: N802
        return self._value

    @dbus_property(access=PropertyAccess.READ)
    def Notifying(self) -> "b":  # type: ignore  # noqa: N802
        return self._notifying

    @method()
    def ReadValue(self, options: "a{sv}") -> "ay":  # type: ignore  # noqa: N802
        device = _option(options, "device")
        peer = str(device) if device else None
        if self.read_callback:
            value = bytes(self.read_callback(peer))
        else:
            value = bytes(self._value)
        offset = _option(options, "offset") or 0
        logger.info("GATT read of %s by %s (offset %s, mtu %s): %d B %s",
                    self._uuid, WriteInfo(peer=peer).address or "a central",
                    offset, _option(options, "mtu") or "?", len(value),
                    value[:32].hex() or "(empty)")
        return bytearray(value)

    @method()
    def WriteValue(self, value: "ay", options: "a{sv}") -> None:  # type: ignore  # noqa: N802
        device = _option(options, "device")
        peer = str(device) if device else None
        if not self.write_guard.accept(len(value), _option(options, "type"), peer):
            return
        self._value = bytearray(value)
        if self.write_callback:
            token = CURRENT_WRITE.set(_write_info(options, peer))
            try:
                self.write_callback(bytes(value), peer)
            finally:
                CURRENT_WRITE.reset(token)

    @method()
    def StartNotify(self) -> None:  # type: ignore  # noqa: N802
        self._notifying = True
        self.emit_properties_changed({"Notifying": True})
        logger.info("GATT subscribe (StartNotify) on %s %s", self._uuid,
                    self.path)

    @method()
    def StopNotify(self) -> None:  # type: ignore  # noqa: N802
        self._notifying = False
        self.emit_properties_changed({"Notifying": False})
        logger.info("GATT unsubscribe (StopNotify) on %s %s", self._uuid,
                    self.path)

    def notify(self, value: bytes) -> None:
        """Update the characteristic and emit a BlueZ notification."""
        self._value = bytearray(value)
        if self._notifying:
            self.emit_properties_changed({"Value": self._value})


class GattService(ServiceInterface):
    """A GATT service exposed over D-Bus."""

    def __init__(self, index: int, uuid: str, primary: bool = True):
        self.path = f"{APP_BASE_PATH}/service{index}"
        self._uuid = uuid
        self._primary = primary
        self.characteristics: list[GattCharacteristic] = []
        super().__init__("org.bluez.GattService1")

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":  # type: ignore  # noqa: N802
        return self._uuid

    @dbus_property(access=PropertyAccess.READ)
    def Primary(self) -> "b":  # type: ignore  # noqa: N802
        return self._primary


class GattApplication(ServiceInterface):
    """ObjectManager for the GATT application."""

    def __init__(self):
        self.path = APP_BASE_PATH
        self.services: list[GattService] = []
        super().__init__("org.freedesktop.DBus.ObjectManager")

    @method()
    def GetManagedObjects(self) -> "a{oa{sa{sv}}}":  # type: ignore  # noqa: N802
        objects: dict[str, dict[str, dict[str, Variant]]] = {}

        for svc in self.services:
            objects[svc.path] = {
                "org.bluez.GattService1": {
                    "UUID": Variant("s", svc._uuid),
                    "Primary": Variant("b", svc._primary),
                },
            }
            for char in svc.characteristics:
                objects[char.path] = {
                    "org.bluez.GattCharacteristic1": {
                        "UUID": Variant("s", char._uuid),
                        "Service": Variant("o", char._service_path),
                        "Flags": Variant("as", char._flags),
                        "Value": Variant("ay", char._value),
                        "Notifying": Variant("b", char._notifying),
                    },
                }

        return objects

    def notify(self, characteristic_uuid: str, value: bytes) -> bool:
        wanted = characteristic_uuid.lower()
        for service in self.services:
            for characteristic in service.characteristics:
                if characteristic._uuid.lower() == wanted:
                    characteristic.notify(value)
                    return True
        return False

    def notify_first(self, value: bytes) -> bool:
        """Notify the profile's first notify-capable characteristic."""
        for service in self.services:
            for characteristic in service.characteristics:
                if "notify" in characteristic._flags:
                    characteristic.notify(value)
                    return True
        return False

    def set_first_read_value(self, value: bytes) -> bool:
        """Update the first read characteristic (Quantum's fff4 snapshot)."""
        for service in self.services:
            for characteristic in service.characteristics:
                if "read" in characteristic._flags:
                    characteristic._value = bytearray(value)
                    return True
        return False

    def set_read_value(self, characteristic_uuid: str, value: bytes) -> bool:
        """Update one specifically named readable characteristic."""
        wanted = characteristic_uuid.lower()
        for service in self.services:
            for characteristic in service.characteristics:
                if (characteristic._uuid.lower() == wanted and
                        "read" in characteristic._flags):
                    characteristic._value = bytearray(value)
                    return True
        return False

    def publish(self, update: GattUpdate | bytes | bytearray) -> None:
        """Publish one decoder result to its intended GATT channels.

        Bare bytes retain the older fault/legacy convention. New stateful
        protocols should return :class:`GattUpdate` so a notification is not
        accidentally installed as the readable snapshot as well.
        """
        if isinstance(update, GattUpdate):
            if update.read_value is not None:
                self.set_first_read_value(update.read_value)
            if update.notification is not None:
                self.notify_first(update.notification)
            return
        value = bytes(update)
        if len(value) > 1 and not value[1] & 0x80:
            self.set_first_read_value(value)
        self.notify_first(value)


def build_application(profile: GattProfile,
                      on_data: Callable[[bytes, str | None], None]) -> GattApplication:
    """Instantiate the D-Bus GATT object tree for a profile.

    The write callback is attached to every characteristic marked
    ``receives_writes`` (exactly one per profile in practice).
    """
    app = GattApplication()
    for svc_index, svc_spec in enumerate(profile.services):
        service = GattService(svc_index, svc_spec.uuid)
        for char_index, char_spec in enumerate(svc_spec.characteristics):
            char = GattCharacteristic(
                char_index, char_spec.uuid, list(char_spec.flags), service.path,
                char_spec.initial_value, char_spec.min_write_gap_ms,
                char_spec.drop_unpaced)
            if char_spec.receives_writes:
                char.write_callback = on_data
            service.characteristics.append(char)
        app.services.append(service)
    return app


def merge_profiles(profiles: list[GattProfile]) -> GattProfile:
    """Return one GATT database containing the union of several profiles."""
    if not profiles:
        raise ValueError("at least one GATT profile is required")
    if all(profile == profiles[0] for profile in profiles[1:]):
        return profiles[0]

    services: dict[str, dict[str, CharacteristicSpec]] = {}
    for profile in profiles:
        for service in profile.services:
            chars = services.setdefault(service.uuid, {})
            for char in service.characteristics:
                previous = chars.get(char.uuid)
                if previous is None:
                    chars[char.uuid] = char
                else:
                    chars[char.uuid] = CharacteristicSpec(
                        uuid=char.uuid,
                        flags=tuple(dict.fromkeys(previous.flags + char.flags)),
                        receives_writes=(previous.receives_writes
                                         or char.receives_writes),
                        initial_value=(previous.initial_value
                                       or char.initial_value),
                        min_write_gap_ms=max(previous.min_write_gap_ms,
                                             char.min_write_gap_ms),
                        drop_unpaced=previous.drop_unpaced or char.drop_unpaced,
                    )
    return GattProfile(
        description=" + ".join(dict.fromkeys(p.description for p in profiles)),
        advertised_uuid=profiles[0].advertised_uuid,
        services=tuple(
            ServiceSpec(uuid, tuple(chars.values()))
            for uuid, chars in services.items()
        ),
    )
