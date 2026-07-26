"""BLE peripheral that emulates a climbing board over BlueZ.

GATT services via the BlueZ D-Bus API (dbus_fast), shaped by the
protocol family's :class:`~ble.gatt.GattProfile`. Advertising via
Adapter.Discoverable + Extended Advertising HCI commands to inject the
family's service UUID into the advertising data; the board identity
travels in the advertised name.

Fail-fast: :func:`preflight_check` verifies that BlueZ (``org.bluez``)
is reachable and a powered-capable adapter exists BEFORE the peripheral
thread starts — a box without Bluetooth aborts with a clear error
instead of hanging silently (a known wart of the predecessor
simulators). ``--list`` and the test suite never touch this module's
runtime path.

Must be run as root (sudo venv/bin/python main.py).
"""

import asyncio
import logging
import os
import subprocess
import threading
from typing import Callable

from dbus_fast.aio import MessageBus
from dbus_fast import BusType, Message, MessageType, Variant

from ble.advertising import disable_extended_adv, set_extended_adv_data
from ble.gatt import GattProfile, build_application

logger = logging.getLogger(__name__)

# BlueZ D-Bus constants
BLUEZ_SERVICE = "org.bluez"
ADAPTER_PATH = "/org/bluez/hci0"
GATT_MANAGER_IFACE = "org.bluez.GattManager1"


class BlueZUnavailableError(RuntimeError):
    """BlueZ or a Bluetooth adapter is missing — live BLE cannot work."""


def should_advertise(link_count: int, multi_connect: bool) -> bool:
    """Whether the board should be advertising right now.

    A peripheral is connectable only WHILE it advertises, so this single
    predicate is what separates the two controller characters the simulator
    can play:

    * ``multi_connect`` — advertise regardless of who is already connected,
      the way a controller with free slots does;
    * exclusive — go quiet as soon as a central is on, which is what real
      Aurora hardware does and what CruxRelay exists for.
    """
    return multi_connect or link_count == 0


async def _async_preflight() -> None:
    """Verify org.bluez is on the system bus and hci0 exists."""
    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    except Exception as exc:
        raise BlueZUnavailableError(
            "cannot connect to the system D-Bus — is this a Linux box "
            f"with D-Bus running? ({exc})"
        ) from exc
    try:
        reply = await bus.call(Message(
            destination=BLUEZ_SERVICE,
            path="/",
            interface="org.freedesktop.DBus.ObjectManager",
            member="GetManagedObjects",
        ))
        if reply is None or reply.message_type == MessageType.ERROR:
            error = reply.error_name if reply else "no reply"
            raise BlueZUnavailableError(
                f"BlueZ (org.bluez) is not available on the system bus "
                f"({error}) — install/start the bluetooth service"
            )
        managed = reply.body[0]
        adapters = [path for path, ifaces in managed.items()
                    if "org.bluez.Adapter1" in ifaces]
        if not adapters:
            raise BlueZUnavailableError(
                "BlueZ is running but no Bluetooth adapter was found "
                "(expected org.bluez.Adapter1 under /org/bluez) — this "
                "machine has no usable Bluetooth hardware"
            )
        if ADAPTER_PATH not in adapters:
            raise BlueZUnavailableError(
                f"no adapter at {ADAPTER_PATH} (found: {adapters}) — "
                "the simulator currently drives hci0 only"
            )
    finally:
        bus.disconnect()


def preflight_check() -> None:
    """Raise :class:`BlueZUnavailableError` unless live BLE can work.

    Called synchronously from main BEFORE the peripheral thread starts,
    so a machine without BlueZ/adapter aborts immediately with a clear
    message instead of advertising into the void.
    """
    asyncio.run(_async_preflight())


class BLEPeripheral:
    """BLE peripheral emulating one climbing board.

    GATT shape and advertised service UUID come from the protocol
    family's :class:`GattProfile`; the advertised name carries the
    board identity.
    """

    def __init__(self, ble_name: str, profile: GattProfile,
                 on_data: Callable[[bytes], None],
                 on_connect: Callable[[], None] | None = None,
                 on_disconnect: Callable[[], None] | None = None,
                 on_fatal: Callable[[BaseException], None] | None = None,
                 multi_connect: bool = False) -> None:
        self._ble_name = ble_name
        self._profile = profile
        self._on_data = on_data
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_fatal = on_fatal
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._bus: MessageBus | None = None
        # "BlueZ reports a connected Device1" — one of two link-state sources,
        # reconciled in _serve(). BlueZ does not create a Device1 object for
        # every central (random resolvable addresses), so this can stay False
        # for the whole lifetime of a real connection.
        self._connected = False
        # "The controller reports an LE connection" — a last-resort source,
        # polled via hcitool. Independent of BlueZ's device bookkeeping, but
        # unreliable in the negative, so it may only ever add a link.
        self._hci_link = False
        # Last known number of connected centrals; the simulator supports
        # several at once, so the count is worth seeing when it changes.
        self._link_count = 0
        # A D-Bus signal and the 2 s poll can land together. Without these,
        # both would see the same change and start two overlapping HCI
        # advertising sequences on the same set.
        self._link_lock = asyncio.Lock()
        self._adv_lock = asyncio.Lock()
        # Which controller character to play — see should_advertise(). Real
        # Aurora hardware is exclusive, so that is the default; multi is for
        # testing the app against a board that takes several clients.
        self._multi_connect = multi_connect
        # Set once _serve() knows them, so a runtime mode change can act.
        self._adv_context: tuple | None = None

    @property
    def multi_connect(self) -> bool:
        return self._multi_connect

    def set_multi_connect(self, enabled: bool) -> None:
        """Switch the connection character while running.

        Takes effect immediately: turning it off silences a board that is
        already connected (nobody else gets in), turning it on brings the
        advertisement back for the next client.
        """
        if enabled == self._multi_connect:
            return
        self._multi_connect = enabled
        logger.info("Connection mode → %s", "multi" if enabled else "single")
        loop, ctx = self._loop, self._adv_context
        if loop is None or ctx is None:
            return
        asyncio.run_coroutine_threadsafe(self._apply_advertising_mode(*ctx), loop)

    async def _apply_advertising_mode(self, adapter_props, uuid: str,
                                      name: str) -> None:
        if should_advertise(self._link_count, self._multi_connect):
            await self._restart_advertising(adapter_props, uuid, name,
                                            reason="mode change")
        else:
            async with self._adv_lock:
                logger.info("Going quiet — exclusive mode with a client connected")
                disable_extended_adv()

    def _handle_gatt_write(self, data: bytes) -> None:
        """Pass a GATT write through to the session decoder."""
        self._on_data(data)

    async def _count_dbus_connections(self) -> int:
        """Number of centrals BlueZ reports as connected to this adapter.

        Asks BlueZ itself (ObjectManager) rather than parsing ``hcitool``,
        which is deprecated and on current kernels prints no LE links at all.
        A silent "no links" from that tool used to read as a disconnect while
        a central was actively writing.
        """
        if self._bus is None:
            return 0
        try:
            reply = await self._bus.call(Message(
                destination=BLUEZ_SERVICE,
                path="/",
                interface="org.freedesktop.DBus.ObjectManager",
                member="GetManagedObjects",
            ))
            if reply is None or reply.message_type == MessageType.ERROR:
                return 0
            managed = reply.body[0]
            return sum(
                1 for path, ifaces in managed.items()
                if path.startswith(ADAPTER_PATH + "/dev_")
                and ifaces.get("org.bluez.Device1", {}).get("Connected")
                and ifaces["org.bluez.Device1"]["Connected"].value
            )
        except Exception as exc:
            logger.debug("D-Bus connection count failed: %s", exc)
            return 0

    async def _check_hci_connections(self) -> bool:
        """Whether the controller reports an LE link, via hcitool.

        Kept as a THIRD opinion only. It cannot distinguish "no link" from
        "this tool does not report LE links on this kernel", so it may only
        ever add a link, never take one away.
        """
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    ["hcitool", "con"], capture_output=True, text=True, timeout=3
                )
            )
            return "< LE" in result.stdout
        except Exception as e:
            logger.debug("hcitool con check failed: %s", e)
            return False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="ble-peripheral")
        self._thread.start()
        logger.info("BLE peripheral thread started")

    def stop(self) -> None:
        # Cooperative shutdown: _serve()'s poll loop checks self._running and
        # returns on its own, so run_until_complete() finishes normally and
        # the bus/loop close cleanly. A forced loop.stop() here interrupted
        # run_until_complete mid-await and raised "Event loop stopped before
        # Future completed" — harmless, but logged as a scary traceback on
        # every board switch.
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        logger.info("BLE peripheral stopped")

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except Exception as exc:
            logger.exception("BLE peripheral event loop error")
            # Fail fast: surface the error to the host instead of leaving
            # a dead peripheral thread behind a live-looking GUI.
            if self._on_fatal is not None and self._running:
                self._on_fatal(exc)
        finally:
            self._loop.close()

    async def _serve(self) -> None:
        board_name = self._ble_name
        logger.info("Starting BLE server as '%s' (%s)",
                    board_name, self._profile.description)

        if os.geteuid() != 0:
            logger.warning(
                "Not running as root! Run with: sudo venv/bin/python main.py")

        # -- Connect to system D-Bus -----------------------------------
        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

        # -- Configure adapter -----------------------------------------
        introspection = await self._bus.introspect(BLUEZ_SERVICE, ADAPTER_PATH)
        adapter = self._bus.get_proxy_object(BLUEZ_SERVICE, ADAPTER_PATH, introspection)
        adapter_props = adapter.get_interface("org.freedesktop.DBus.Properties")

        try:
            await adapter_props.call_set(
                "org.bluez.Adapter1", "Powered", Variant("b", True))
            logger.info("Adapter powered on")
        except Exception as e:
            logger.warning("Could not power on adapter: %s", e)

        try:
            await adapter_props.call_set(
                "org.bluez.Adapter1", "Alias", Variant("s", board_name))
            logger.info("Adapter alias set to '%s'", board_name)
        except Exception as e:
            logger.warning("Could not set Adapter.Alias: %s", e)

        # -- Register GATT application ---------------------------------
        app = build_application(self._profile, self._handle_gatt_write)

        # Export all GATT objects on D-Bus
        self._bus.export(app.path, app)
        for svc in app.services:
            self._bus.export(svc.path, svc)
            for char in svc.characteristics:
                self._bus.export(char.path, char)

        gatt_mgr = adapter.get_interface(GATT_MANAGER_IFACE)
        await gatt_mgr.call_register_application(app.path, {})  # type: ignore
        logger.info("GATT application registered (%s)", self._profile.description)

        # -- Start advertising -----------------------------------------
        # Step 1: Set Discoverable (starts legacy advertising on handle 0x00)
        try:
            await adapter_props.call_set(
                "org.bluez.Adapter1", "DiscoverableTimeout", Variant("u", 0))
            await adapter_props.call_set(
                "org.bluez.Adapter1", "Discoverable", Variant("b", True))
            logger.info("Adapter.Discoverable set")
        except Exception as e:
            logger.warning("Could not set Discoverable: %s", e)

        # Step 2: Wait for BlueZ to finish programming the controller
        await asyncio.sleep(1.0)

        # Step 3: Overwrite handle 0x00's advertising data via Extended
        # Advertising HCI commands so the advertised service UUID is the
        # family's discovery UUID (Aurora: the 4488B571 discovery service;
        # MoonBoard: the Nordic UART Service itself).
        uuid = self._profile.advertised_uuid
        if set_extended_adv_data(uuid, board_name):
            logger.info("Service UUID injected into advertising data!")
        else:
            logger.warning(
                "Could not inject UUID. Device visible as '%s' "
                "but apps that filter on the UUID may not discover it.",
                board_name)

        logger.info("Advertising UUID: %s", uuid)
        logger.info("Connection mode: %s",
                    "multi" if self._multi_connect else "single (exclusive)")
        # Lets set_multi_connect() act on a running peripheral.
        self._adv_context = (adapter_props, uuid, board_name)
        logger.info("BLE peripheral ready — waiting for connections")

        # -- Monitor connection state ----------------------------------
        # Every signal and every poll leads to the same question — how many
        # centrals does BlueZ currently see? — because the answer drives two
        # different things: the connect/disconnect callbacks (an edge) and
        # advertising (a count).
        #
        # Counting rather than tracking one device matters for the multi-client
        # case: a single Device1 going Connected=False says nothing about the
        # OTHER centrals still on the adapter, and treating it as "the link is
        # down" cleared the board state under a client that was still there.
        #
        # hcitool remains only as a last resort for a central BlueZ never
        # created a Device1 for. It cannot tell "no LE link" from "this kernel's
        # hcitool does not print LE links", so it may only ever ADD a link.
        link_up = False

        def _reconcile_link(now_up: bool, source: str) -> None:
            nonlocal link_up
            if now_up == link_up:
                return
            link_up = now_up
            if now_up:
                logger.info("Central connected (%s)", source)
                if self._on_connect:
                    self._on_connect()
            else:
                logger.info("Central disconnected (%s)", source)
                if self._on_disconnect:
                    self._on_disconnect()

        async def _recount(source: str) -> None:
            async with self._link_lock:
                count = await self._count_dbus_connections()
                self._hci_link = (
                    await self._check_hci_connections() if count == 0 else False
                )
                self._connected = count > 0
                effective = count if count else (1 if self._hci_link else 0)
                changed = effective != self._link_count
                self._link_count = effective
                _reconcile_link(self._connected or self._hci_link, source)
                if not changed:
                    return
                logger.info("%d central(s) connected (%s)", effective, source)
                # A connection ends the advertisement that carried it (a legacy
                # ADV_IND is consumed by the CONNECT_IND), and a peripheral can
                # only be connected to WHILE it advertises. So in multi mode
                # EVERY change puts it back up — otherwise the board goes
                # silent after the first central and no second one can join,
                # however many slots the controller has free. In exclusive
                # mode only the last disconnect does, which is what real Aurora
                # hardware looks like from the outside.
                if should_advertise(effective, self._multi_connect):
                    asyncio.ensure_future(self._restart_advertising(
                        adapter_props, uuid, board_name, reason=source))

        def _on_properties_changed(msg: Message) -> None:
            if msg.message_type != MessageType.SIGNAL:
                return
            if msg.member != "PropertiesChanged":
                return
            path = msg.path or ""
            if not path.startswith(ADAPTER_PATH + "/dev_"):
                return

            args = msg.body
            if not args or len(args) < 2:
                return
            iface = args[0]
            changed = args[1]
            if iface != "org.bluez.Device1":
                return
            if "Connected" not in changed:
                return

            # Re-ask instead of trusting this one device's new value — see
            # the multi-client note above.
            asyncio.ensure_future(_recount(f"D-Bus {path}"))

        self._bus.add_message_handler(_on_properties_changed)

        # Subscribe to PropertiesChanged signals from BlueZ
        await self._bus.call(Message(
            destination="org.freedesktop.DBus",
            path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus",
            member="AddMatch",
            signature="s",
            body=[
                "type='signal',"
                "sender='org.bluez',"
                "interface='org.freedesktop.DBus.Properties',"
                "member='PropertiesChanged',"
                f"path_namespace='{ADAPTER_PATH}'"
            ],
        ))
        logger.info("D-Bus signal monitoring active (connect/disconnect)")

        # -- Keep running ----------------------------------------------
        try:
            poll_ticks = 0
            while self._running:
                await asyncio.sleep(0.5)
                poll_ticks += 1

                # Every 2s, unconditionally: a signal can be missed, and gating
                # the poll on prior activity is what let a silent connect /
                # disconnect slip through unnoticed.
                if poll_ticks >= 4:
                    poll_ticks = 0
                    await _recount("poll")
        finally:
            # Explicitly tear the GATT app + advertising down on this loop so a
            # board switch leaves NO stale application registered with BlueZ.
            # (Relying on the dropped D-Bus socket alone left the old app live:
            # BlueZ then served two UART services, and the phone's writes went
            # to the previous board's RX characteristic — climbs silently lost.)
            await self._unregister(gatt_mgr, app, adapter_props)

    async def _unregister(self, gatt_mgr, app, adapter_props) -> None:
        """Best-effort GATT/advertising teardown, all on the serve loop."""
        try:
            await gatt_mgr.call_unregister_application(app.path)  # type: ignore
            logger.info("GATT application unregistered")
        except Exception as exc:
            logger.debug("unregister_application failed: %s", exc)
        try:
            await adapter_props.call_set(
                "org.bluez.Adapter1", "Discoverable", Variant("b", False))
        except Exception as exc:
            logger.debug("clearing Discoverable failed: %s", exc)
        try:
            self._bus.unexport(app.path)
            for svc in app.services:
                self._bus.unexport(svc.path)
                for char in svc.characteristics:
                    self._bus.unexport(char.path)
        except Exception as exc:
            logger.debug("unexport failed: %s", exc)
        try:
            self._bus.disconnect()
            logger.info("D-Bus connection closed")
        except Exception as exc:
            logger.debug("bus disconnect failed: %s", exc)

    async def _restart_advertising(self, adapter_props, uuid: str, name: str,
                                   reason: str = "") -> None:
        """Put advertising back up so (further) clients can connect."""
        async with self._adv_lock:
            logger.info("Restarting advertising (%s)...", reason or "link change")
            await asyncio.sleep(1.0)  # Give BlueZ time to clean up

            # Re-enable Discoverable (BlueZ may have turned it off)
            try:
                await adapter_props.call_set(
                    "org.bluez.Adapter1", "Discoverable", Variant("b", True))
                logger.info("Adapter.Discoverable re-enabled")
            except Exception as e:
                logger.warning("Could not re-enable Discoverable: %s", e)

            await asyncio.sleep(0.5)

            # Re-inject UUID into advertising data. On a controller that is out
            # of connection slots the enable command fails — that is a correct
            # refusal, not an error to work around.
            if set_extended_adv_data(uuid, name):
                logger.info("Advertising restarted with UUID")
            else:
                logger.warning("Could not re-inject UUID into advertising")
