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


# ---------------------------------------------------------------------------
# D-Bus GATT objects (BlueZ reads these through GetManagedObjects)
# ---------------------------------------------------------------------------

class GattCharacteristic(ServiceInterface):
    """A single GATT characteristic exposed over D-Bus."""

    def __init__(self, index: int, uuid: str, flags: list[str],
                 service_path: str):
        self.path = f"{service_path}/char{index}"
        self._uuid = uuid
        self._flags = flags
        self._service_path = service_path
        self._value = bytearray()
        self.write_callback: Callable[[bytes], None] | None = None
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

    @method()
    def ReadValue(self, options: "a{sv}") -> "ay":  # type: ignore  # noqa: N802
        return self._value

    @method()
    def WriteValue(self, value: "ay", options: "a{sv}") -> None:  # type: ignore  # noqa: N802
        self._value = bytearray(value)
        if self.write_callback:
            self.write_callback(bytes(value))

    @method()
    def StartNotify(self) -> None:  # type: ignore  # noqa: N802
        # Notify-only stubs (MoonBoard TX): a real board notifies the
        # phone here, but the apps only ever write. Accept the
        # subscription so the central is happy; never push anything.
        logger.debug("StartNotify on %s (stub — no notifications sent)", self.path)

    @method()
    def StopNotify(self) -> None:  # type: ignore  # noqa: N802
        logger.debug("StopNotify on %s", self.path)


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
                    },
                }

        return objects


def build_application(profile: GattProfile,
                      on_data: Callable[[bytes], None]) -> GattApplication:
    """Instantiate the D-Bus GATT object tree for a profile.

    The write callback is attached to every characteristic marked
    ``receives_writes`` (exactly one per profile in practice).
    """
    app = GattApplication()
    for svc_index, svc_spec in enumerate(profile.services):
        service = GattService(svc_index, svc_spec.uuid)
        for char_index, char_spec in enumerate(svc_spec.characteristics):
            char = GattCharacteristic(
                char_index, char_spec.uuid, list(char_spec.flags), service.path)
            if char_spec.receives_writes:
                char.write_callback = on_data
            service.characteristics.append(char)
        app.services.append(service)
    return app
