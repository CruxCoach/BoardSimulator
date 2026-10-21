"""--readvertise-delay: a released board stays silent for a while.

Real Aurora boxes often take more than 4 s to re-advertise after a link
ends (Boardsesh #3609). An app that connects to the first board it sees
then picks a neighbouring wall; two virtual boards plus this delay
reproduce that without hardware.
"""

import asyncio

from ble.multiplex import MultiplexedBLEPeripheral
from ble.peripheral import readvertise_delay
from tests.test_multi_board import make_board


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_delay_applies_only_after_the_last_central_left() -> None:
    assert readvertise_delay(0, False, 5.0) == 5.0
    assert readvertise_delay(1, False, 5.0) == 0.0
    assert readvertise_delay(0, True, 5.0) == 0.0, "multi-connect never stops advertising"
    assert readvertise_delay(0, False, -1.0) == 0.0


def connect_and_release(mux: MultiplexedBLEPeripheral, slot: int) -> None:
    device = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    mux._assign_device(device, slot)

    async def nobody() -> dict:
        return {}

    mux._connected_devices = nobody
    asyncio.run(mux._reconcile_connections())


def test_released_board_stays_silent_for_the_delay_while_the_other_advertises() -> None:
    clock = FakeClock()
    mux = MultiplexedBLEPeripheral([
        make_board("first", "Kilter Board"),
        make_board("second", "Kilter Board"),
    ], readvertise_delay_s=5.0, clock=clock)
    connect_and_release(mux, 0)

    assert not mux._desired_enabled(0), "wall A is still silent"
    assert mux._desired_enabled(1), "wall B keeps advertising"
    clock.now += 4.9
    assert not mux._desired_enabled(0)
    clock.now += 0.2
    assert mux._desired_enabled(0)


def test_without_a_delay_the_board_returns_at_once() -> None:
    clock = FakeClock()
    mux = MultiplexedBLEPeripheral([
        make_board("first", "Kilter Board#a001@3"),
        make_board("second", "Kilter Board#b002@3"),
    ], clock=clock)
    connect_and_release(mux, 0)
    assert mux._desired_enabled(0)


def test_sync_keeps_polling_until_the_quiet_window_ends(monkeypatch) -> None:
    import ble.multiplex as multiplex

    monkeypatch.setattr(multiplex, "set_adv_sets_enabled", lambda *args: True)
    clock = FakeClock()
    mux = MultiplexedBLEPeripheral([
        make_board("first", "Kilter Board"),
        make_board("second", "Kilter Board"),
    ], readvertise_delay_s=5.0, clock=clock)
    mux._enabled = [True, True]
    connect_and_release(mux, 0)

    asyncio.run(mux._sync_advertising())
    assert mux._enabled == [False, True] and mux._sync_needed
    clock.now += 6.0
    asyncio.run(mux._sync_advertising())
    assert mux._enabled == [True, True] and not mux._sync_needed
