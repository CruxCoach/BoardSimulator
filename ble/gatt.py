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
import logging
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


# ---------------------------------------------------------------------------
# D-Bus GATT objects (BlueZ reads these through GetManagedObjects)
# ---------------------------------------------------------------------------

class GattCharacteristic(ServiceInterface):
    """A single GATT characteristic exposed over D-Bus."""

    def __init__(self, index: int, uuid: str, flags: list[str],
                 service_path: str, initial_value: bytes = b""):
        self.path = f"{service_path}/char{index}"
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
        if self.read_callback:
            device_option = options.get("device")
            device = getattr(device_option, "value", device_option)
            return bytearray(self.read_callback(str(device) if device else None))
        return self._value

    @method()
    def WriteValue(self, value: "ay", options: "a{sv}") -> None:  # type: ignore  # noqa: N802
        self._value = bytearray(value)
        if self.write_callback:
            device_option = options.get("device")
            device = getattr(device_option, "value", device_option)
            self.write_callback(bytes(value), str(device) if device else None)

    @method()
    def StartNotify(self) -> None:  # type: ignore  # noqa: N802
        self._notifying = True
        self.emit_properties_changed({"Notifying": True})
        logger.debug("StartNotify on %s", self.path)

    @method()
    def StopNotify(self) -> None:  # type: ignore  # noqa: N802
        self._notifying = False
        self.emit_properties_changed({"Notifying": False})
        logger.debug("StopNotify on %s", self.path)

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
                char_spec.initial_value)
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
                    )
    return GattProfile(
        description=" + ".join(dict.fromkeys(p.description for p in profiles)),
        advertised_uuid=profiles[0].advertised_uuid,
        services=tuple(
            ServiceSpec(uuid, tuple(chars.values()))
            for uuid, chars in services.items()
        ),
    )
