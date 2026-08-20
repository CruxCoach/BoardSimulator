"""Per-adapter scoping — two controllers, two independent BLE realms.

A host with two adapters can run two simulator processes at once, one
board each. Nothing may leak between them: not the BlueZ object paths,
not the centrals counted, not the D-Bus signal subscription and above all
not the HCI commands that program the advertising set — an unpinned
``hcitool`` would happily reprogram the neighbour's controller.

Everything here runs without Bluetooth hardware, root or a live bus;
that is the whole point — the wiring is *derived* from the adapter name,
so it can be asserted on a laptop with no adapter at all.
"""

import asyncio
import subprocess

import pytest
from dbus_fast import MessageType, Variant

from ble import advertising, peripheral
from ble.adapter import (
    DEFAULT_ADAPTER,
    InvalidAdapterError,
    adapter_path,
    device_path_prefix,
    device_signal_match_rule,
    hci_args,
    normalize_adapter,
)
from ble.gatt import GattProfile

PROFILE = GattProfile(description="test profile", advertised_uuid="1234")


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeRun:
    """Stand-in for subprocess.run that records every argv it is handed."""

    def __init__(self, returncodes: list[int] | None = None,
                 stdout: str = "") -> None:
        self.calls: list[list[str]] = []
        self._codes = list(returncodes or [])
        self._stdout = stdout

    def __call__(self, cmd, **kwargs) -> subprocess.CompletedProcess:
        self.calls.append(list(cmd))
        code = self._codes.pop(0) if self._codes else 0
        return subprocess.CompletedProcess(cmd, code, self._stdout, "")

    @property
    def adapters_used(self) -> set[str]:
        """Every controller the recorded calls were aimed at."""
        return {call[call.index("-i") + 1] for call in self.calls
                if "-i" in call}


class _Reply:
    message_type = MessageType.METHOD_RETURN

    def __init__(self, body: list) -> None:
        self.body = body


class FakeBus:
    """Enough MessageBus to answer one GetManagedObjects call."""

    def __init__(self, managed: dict) -> None:
        self._managed = managed
        self.unexported: list[str] = []
        self.disconnected = False

    async def call(self, msg) -> _Reply:
        return _Reply([self._managed])

    def unexport(self, path: str) -> None:
        self.unexported.append(path)

    def disconnect(self) -> None:
        self.disconnected = True


class FakeProps:
    """org.freedesktop.DBus.Properties proxy that records its writes."""

    def __init__(self) -> None:
        self.sets: list[tuple[str, object]] = []

    async def call_set(self, iface: str, prop: str, variant) -> None:
        self.sets.append((prop, variant.value))


class FakeGattManager:
    def __init__(self) -> None:
        self.unregistered: list[str] = []

    async def call_unregister_application(self, path: str) -> None:
        self.unregistered.append(path)


class FakeApp:
    path = "/org/boardsim/app"
    services: tuple = ()


def managed_objects(connected: tuple[str, ...] = (),
                    disconnected: tuple[str, ...] = ()) -> dict:
    """A GetManagedObjects reply body with devices on both adapters."""
    objs: dict = {
        "/org/bluez/hci0": {"org.bluez.Adapter1": {}},
        "/org/bluez/hci1": {"org.bluez.Adapter1": {}},
    }
    for path in connected:
        objs[path] = {"org.bluez.Device1": {"Connected": Variant("b", True)}}
    for path in disconnected:
        objs[path] = {"org.bluez.Device1": {"Connected": Variant("b", False)}}
    return objs


def make_peripheral(adapter: str = DEFAULT_ADAPTER) -> peripheral.BLEPeripheral:
    return peripheral.BLEPeripheral(
        ble_name="Kilter Board#a001@3", profile=PROFILE,
        on_data=lambda data: None, adapter=adapter,
    )


def test_protocol_replies_are_forwarded_to_quantum_notify_characteristic() -> None:
    sent: list[bytes] = []

    class NotifyApp:
        def set_first_read_value(self, value: bytes) -> None:
            pass

        def notify_first(self, value: bytes) -> None:
            sent.append(value)

    ble = peripheral.BLEPeripheral(
        ble_name="QuantumXL_test", profile=PROFILE,
        on_data=lambda data: [b"state", b"ack"])
    ble._app = NotifyApp()
    ble._handle_gatt_write(b"command")

    assert sent == [b"state", b"ack"]


@pytest.fixture
def instant_sleep(monkeypatch):
    """Skip the advertising restart's BlueZ settling delays.

    Only asyncio.sleep is replaced; the event loop itself is untouched, so
    the coroutine under test runs its real sequence, just without the 1.5 s.
    """
    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)


# ---------------------------------------------------------------------------
# Adapter names
# ---------------------------------------------------------------------------

class TestAdapterNames:
    def test_default_is_the_first_controller(self) -> None:
        assert DEFAULT_ADAPTER == "hci0"
        assert normalize_adapter() == "hci0"
        assert normalize_adapter(None) == "hci0"

    @pytest.mark.parametrize("name", ["hci0", "hci1", "hci2", "hci10", "hci42"])
    def test_controller_names_pass_through(self, name: str) -> None:
        assert normalize_adapter(name) == name

    def test_case_and_padding_are_normalised(self) -> None:
        assert normalize_adapter(" HCI1 ") == "hci1"
        assert normalize_adapter("Hci0\n") == "hci0"

    @pytest.mark.parametrize("name", [
        "", "   ", "hci", "hcix", "eth0", "0", "1", "hci 1", "hci-1",
        "hci01",              # one canonical spelling per controller
        "hci0/dev_AA_BB",     # would widen the D-Bus path namespace
        "../org/bluez/hci1",  # would escape into the other realm
        "hci0'",              # would break out of the match-rule quoting
        "hci0 hci1",
    ])
    def test_anything_that_is_not_a_controller_is_rejected(self, name) -> None:
        with pytest.raises(InvalidAdapterError):
            normalize_adapter(name)

    @pytest.mark.parametrize("value", [0, 1, b"hci0", ["hci0"]])
    def test_non_strings_are_rejected(self, value) -> None:
        with pytest.raises(InvalidAdapterError):
            normalize_adapter(value)

    def test_error_names_the_offending_value(self) -> None:
        with pytest.raises(InvalidAdapterError) as exc:
            normalize_adapter("wlan0")
        assert "wlan0" in str(exc.value)
        assert "hciN" in str(exc.value)


class TestDerivedPaths:
    def test_adapter_path(self) -> None:
        assert adapter_path("hci0") == "/org/bluez/hci0"
        assert adapter_path("hci1") == "/org/bluez/hci1"
        assert adapter_path() == "/org/bluez/hci0"

    def test_device_prefix_is_not_a_prefix_of_another_adapter(self) -> None:
        # "/org/bluez/hci1" starts with "/org/bluez/hci1"… but hci10's
        # devices must not count as hci1's, hence the trailing "/dev_".
        assert device_path_prefix("hci1") == "/org/bluez/hci1/dev_"
        assert not device_path_prefix("hci10").startswith(
            device_path_prefix("hci1"))

    def test_hci_args_pin_one_controller(self) -> None:
        assert hci_args("hci1") == ["-i", "hci1"]
        assert hci_args() == ["-i", "hci0"]

    def test_match_rule_is_scoped_to_its_adapter(self) -> None:
        rule = device_signal_match_rule("hci1")
        assert "path_namespace='/org/bluez/hci1'" in rule
        assert "member='PropertiesChanged'" in rule
        assert "/org/bluez/hci0" not in rule

    def test_derived_values_are_disjoint_between_realms(self) -> None:
        assert adapter_path("hci0") != adapter_path("hci1")
        assert device_path_prefix("hci0") != device_path_prefix("hci1")
        assert hci_args("hci0") != hci_args("hci1")
        assert device_signal_match_rule("hci0") != device_signal_match_rule("hci1")

    def test_invalid_names_never_reach_a_path_or_argv(self) -> None:
        for builder in (adapter_path, device_path_prefix, hci_args,
                        device_signal_match_rule):
            with pytest.raises(InvalidAdapterError):
                builder("hci0; rm -rf /")


# ---------------------------------------------------------------------------
# HCI commands
# ---------------------------------------------------------------------------

class TestHcitoolPinning:
    def test_disable_targets_the_given_adapter(self, monkeypatch) -> None:
        fake = FakeRun()
        monkeypatch.setattr(advertising.subprocess, "run", fake)

        assert advertising.disable_extended_adv("hci1") is True

        assert fake.calls == [
            ["hcitool", "-i", "hci1", "cmd", "0x08", "0x0039",
             "00", "01", "00", "00", "00", "00"]
        ]

    def test_disable_defaults_to_hci0(self, monkeypatch) -> None:
        fake = FakeRun()
        monkeypatch.setattr(advertising.subprocess, "run", fake)

        advertising.disable_extended_adv()

        assert fake.adapters_used == {"hci0"}

    def test_every_step_of_the_setup_is_pinned(self, monkeypatch) -> None:
        fake = FakeRun()
        monkeypatch.setattr(advertising.subprocess, "run", fake)

        assert advertising.set_extended_adv_data("1234", "Board", "hci1") is True

        # disable, params, data, scan response, enable — all five, one realm.
        assert len(fake.calls) == 5
        assert fake.adapters_used == {"hci1"}
        for call in fake.calls:
            assert call[:4] == ["hcitool", "-i", "hci1", "cmd"]

    def test_setup_defaults_to_hci0_for_callers_that_pass_no_adapter(
            self, monkeypatch) -> None:
        fake = FakeRun()
        monkeypatch.setattr(advertising.subprocess, "run", fake)

        advertising.set_extended_adv_data("1234", "Board")

        assert fake.adapters_used == {"hci0"}

    def test_data_only_fallback_stays_on_its_adapter(self, monkeypatch) -> None:
        # First call (the disable) fails → the data-only fallback runs.
        fake = FakeRun(returncodes=[1])
        monkeypatch.setattr(advertising.subprocess, "run", fake)

        assert advertising.set_extended_adv_data("1234", "Board", "hci1") is True

        assert fake.adapters_used == {"hci1"}
        assert [call[5] for call in fake.calls] == ["0x0039", "0x0037", "0x0038"]

    def test_bad_adapter_never_spawns_a_process(self, monkeypatch) -> None:
        fake = FakeRun()
        monkeypatch.setattr(advertising.subprocess, "run", fake)

        with pytest.raises(InvalidAdapterError):
            advertising.disable_extended_adv("wlan0")

        assert fake.calls == []


# ---------------------------------------------------------------------------
# Peripheral instance scoping
# ---------------------------------------------------------------------------

class TestPeripheralAdapter:
    def test_defaults_to_hci0(self) -> None:
        ble = make_peripheral()
        assert ble.adapter == "hci0"
        assert ble.adapter_path == "/org/bluez/hci0"

    def test_carries_the_adapter_it_was_given(self) -> None:
        ble = make_peripheral("hci1")
        assert ble.adapter == "hci1"
        assert ble.adapter_path == "/org/bluez/hci1"

    def test_normalises_the_name(self) -> None:
        assert make_peripheral(" HCI2 ").adapter == "hci2"

    def test_rejects_a_bad_adapter_at_construction(self) -> None:
        # Fail at startup, not inside the BLE thread half a second later.
        with pytest.raises(InvalidAdapterError):
            make_peripheral("wlan0")

    def test_thread_is_named_after_its_adapter(self, monkeypatch) -> None:
        # Two realms on one host produce two log streams; the thread name
        # is what tells them apart in a stack dump.
        created: dict = {}

        class _FakeThread:
            def __init__(self, **kwargs) -> None:
                created.update(kwargs)

            def start(self) -> None:
                pass

        monkeypatch.setattr(peripheral.threading, "Thread", _FakeThread)
        make_peripheral("hci1").start()

        assert created["name"] == "ble-peripheral-hci1"

    def test_owns_only_its_own_adapters_devices(self) -> None:
        ble = make_peripheral("hci1")
        assert ble._owns_device_path("/org/bluez/hci1/dev_AA_BB_CC_DD_EE_FF")
        assert not ble._owns_device_path("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        assert not ble._owns_device_path("/org/bluez/hci10/dev_AA_BB_CC_DD_EE_FF")
        assert not ble._owns_device_path("/org/bluez/hci1")


class TestConnectionCounting:
    def test_counts_only_centrals_on_its_own_adapter(self) -> None:
        managed = managed_objects(connected=(
            "/org/bluez/hci0/dev_AA_AA_AA_AA_AA_AA",
            "/org/bluez/hci0/dev_BB_BB_BB_BB_BB_BB",
            "/org/bluez/hci1/dev_CC_CC_CC_CC_CC_CC",
        ))

        first = make_peripheral("hci0")
        second = make_peripheral("hci1")
        first._bus = FakeBus(managed)
        second._bus = FakeBus(managed)

        assert asyncio.run(first._count_dbus_connections()) == 2
        assert asyncio.run(second._count_dbus_connections()) == 1

    def test_a_neighbours_central_is_invisible(self) -> None:
        # The failure this guards: hci1's board clearing itself because a
        # phone connected to hci0's board.
        managed = managed_objects(connected=(
            "/org/bluez/hci0/dev_AA_AA_AA_AA_AA_AA",))

        ble = make_peripheral("hci1")
        ble._bus = FakeBus(managed)

        assert asyncio.run(ble._count_dbus_connections()) == 0

    def test_disconnected_devices_do_not_count(self) -> None:
        managed = managed_objects(
            connected=("/org/bluez/hci1/dev_AA_AA_AA_AA_AA_AA",),
            disconnected=("/org/bluez/hci1/dev_BB_BB_BB_BB_BB_BB",),
        )
        ble = make_peripheral("hci1")
        ble._bus = FakeBus(managed)

        assert asyncio.run(ble._count_dbus_connections()) == 1

    def test_hci_fallback_is_pinned_to_its_adapter(self, monkeypatch) -> None:
        fake = FakeRun(stdout="\t< LE 12:34:56:78:9A:BC handle 64 state 1")
        monkeypatch.setattr(peripheral.subprocess, "run", fake)

        ble = make_peripheral("hci1")
        assert asyncio.run(ble._check_hci_connections()) is True

        assert fake.calls == [["hcitool", "-i", "hci1", "con"]]

    def test_hci_fallback_defaults_to_hci0(self, monkeypatch) -> None:
        fake = FakeRun(stdout="")
        monkeypatch.setattr(peripheral.subprocess, "run", fake)

        asyncio.run(make_peripheral()._check_hci_connections())

        assert fake.calls == [["hcitool", "-i", "hci0", "con"]]


class TestAdvertisingIsPerRealm:
    def test_restart_reprograms_only_its_own_controller(
            self, monkeypatch, instant_sleep) -> None:
        recorded: list[tuple[str, str, str]] = []
        monkeypatch.setattr(
            peripheral, "set_extended_adv_data",
            lambda uuid, name, adapter: recorded.append((uuid, name, adapter))
            or True)

        ble = make_peripheral("hci1")
        props = FakeProps()
        asyncio.run(ble._restart_advertising(props, "1234", "Board", "test"))

        assert recorded == [("1234", "Board", "hci1")]
        assert ("Discoverable", True) in props.sets

    def test_going_quiet_silences_only_its_own_controller(
            self, monkeypatch) -> None:
        disabled: list[str] = []
        monkeypatch.setattr(peripheral, "disable_extended_adv",
                            lambda adapter: disabled.append(adapter) or True)

        # Exclusive mode with a central on → this realm stops advertising.
        ble = make_peripheral("hci1")
        ble._link_count = 1
        asyncio.run(ble._apply_advertising_mode(FakeProps(), "1234", "Board"))

        assert disabled == ["hci1"]

    def test_multi_mode_restarts_on_its_own_controller(
            self, monkeypatch, instant_sleep) -> None:
        recorded: list[str] = []
        monkeypatch.setattr(
            peripheral, "set_extended_adv_data",
            lambda uuid, name, adapter: recorded.append(adapter) or True)

        ble = make_peripheral("hci1")
        ble._multi_connect = True
        ble._link_count = 1
        asyncio.run(ble._apply_advertising_mode(FakeProps(), "1234", "Board"))

        assert recorded == ["hci1"]


class TestTeardownIsPerRealm:
    def test_teardown_leaves_the_other_realm_advertising(
            self, monkeypatch) -> None:
        disabled: list[str] = []
        monkeypatch.setattr(peripheral, "disable_extended_adv",
                            lambda adapter: disabled.append(adapter) or True)

        ble = make_peripheral("hci1")
        bus = FakeBus(managed_objects())
        ble._bus = bus
        gatt_mgr, app, props = FakeGattManager(), FakeApp(), FakeProps()

        asyncio.run(ble._unregister(gatt_mgr, app, props))

        # Its own GATT app gone, its own advertising down, its own bus
        # closed — and not one command aimed at hci0.
        assert gatt_mgr.unregistered == [FakeApp.path]
        assert ("Discoverable", False) in props.sets
        assert disabled == ["hci1"]
        assert bus.disconnected is True

    def test_teardown_survives_a_dead_controller(self, monkeypatch) -> None:
        # A failing hcitool must not abort the rest of the cleanup.
        def _boom(adapter: str) -> bool:
            raise OSError("hcitool exploded")

        monkeypatch.setattr(peripheral, "disable_extended_adv", _boom)

        ble = make_peripheral("hci1")
        bus = FakeBus(managed_objects())
        ble._bus = bus

        asyncio.run(ble._unregister(FakeGattManager(), FakeApp(), FakeProps()))

        assert bus.disconnected is True


class TestTwoRealmsCoexist:
    """The end-to-end claim: hci0 and hci1 share nothing but the host."""

    def test_every_adapter_derived_value_differs(self) -> None:
        first = make_peripheral("hci0")
        second = make_peripheral("hci1")

        assert first.adapter != second.adapter
        assert first.adapter_path != second.adapter_path
        assert first._device_prefix != second._device_prefix
        assert (device_signal_match_rule(first.adapter)
                != device_signal_match_rule(second.adapter))

    def test_neither_peripheral_claims_the_others_devices(self) -> None:
        first = make_peripheral("hci0")
        second = make_peripheral("hci1")
        path0 = "/org/bluez/hci0/dev_AA_AA_AA_AA_AA_AA"
        path1 = "/org/bluez/hci1/dev_BB_BB_BB_BB_BB_BB"

        assert first._owns_device_path(path0)
        assert not first._owns_device_path(path1)
        assert second._owns_device_path(path1)
        assert not second._owns_device_path(path0)

    def test_connection_state_is_independent(self) -> None:
        # One realm counting a central must not move the other's count.
        managed = managed_objects(connected=(
            "/org/bluez/hci0/dev_AA_AA_AA_AA_AA_AA",))
        first, second = make_peripheral("hci0"), make_peripheral("hci1")
        first._bus, second._bus = FakeBus(managed), FakeBus(managed)

        assert asyncio.run(first._count_dbus_connections()) == 1
        assert asyncio.run(second._count_dbus_connections()) == 0
        assert first._link_count == second._link_count == 0  # untouched by a count
