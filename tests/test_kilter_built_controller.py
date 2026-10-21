"""The Kilter-built controller generation (shipped since mid-2025).

Such a box advertises a bare "Kilter Board" (no '#serial@apiLevel'),
speaks API level 2 and takes write-with-response only; the Kilter app
writes it in 20-byte chunks 100 ms apart. These tests pin its identity,
GATT shape, option validation and the write checks of :class:`WriteGuard`.
"""

import logging
import os
import subprocess
import sys

import pytest

import config
from ble.gatt import WriteGuard, merge_profiles
from boards import board_for
from protocols.session import (
    AURORA_GATT_PROFILE,
    create_session,
    kilter_built_gatt_profile,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def kilter_built(board_key="kilter", layout=None, size_id=None, **kwargs):
    board = board_for(board_key)
    return create_session(
        board, board.variant_for(layout), size_id,
        kwargs.get("api_level"), kwargs.get("serial"),
        config.CONTROLLER_KILTER_BUILT, kwargs.get("strict_pacing", False))


def rx_of(profile):
    (rx,) = [c for s in profile.services for c in s.characteristics
             if c.receives_writes]
    return rx


class TestIdentity:
    def test_kilter_built_box_advertises_the_bare_product_name(self) -> None:
        session = kilter_built()
        assert session.ble_name == "Kilter Board"
        assert "#" not in session.ble_name and "@" not in session.ble_name

    def test_other_aurora_families_use_their_own_bare_name(self) -> None:
        assert kilter_built("tension").ble_name == board_for("tension").display_name

    def test_kilter_built_box_speaks_api_level_2(self) -> None:
        assert kilter_built().api_level == config.KILTER_BUILT_API_LEVEL == 2

    def test_classic_box_is_unchanged_by_default(self) -> None:
        board = board_for("kilter")
        session = create_session(board, board.variant_for(None), None, None, None)
        assert session.controller == config.CONTROLLER_AURORA
        assert session.ble_name == "Kilter Board#0001@3"
        assert session.gatt_profile is AURORA_GATT_PROFILE


class TestGattShape:
    def test_rx_takes_write_with_response_only(self) -> None:
        rx = rx_of(kilter_built().gatt_profile)
        assert rx.uuid == config.RX_CHARACTERISTIC_UUID
        assert rx.flags == ("write",)
        assert rx.min_write_gap_ms == config.KILTER_APP_CHUNK_GAP_MS == 100
        assert not rx.drop_unpaced

    def test_services_and_advertised_uuid_match_the_classic_box(self) -> None:
        profile = kilter_built().gatt_profile
        assert profile.advertised_uuid == AURORA_GATT_PROFILE.advertised_uuid
        assert [s.uuid for s in profile.services] == [
            s.uuid for s in AURORA_GATT_PROFILE.services]

    def test_strict_pacing_drops_unpaced_writes(self) -> None:
        assert rx_of(kilter_built(strict_pacing=True).gatt_profile).drop_unpaced

    def test_merge_keeps_the_pacing_of_either_profile(self) -> None:
        merged = merge_profiles([AURORA_GATT_PROFILE, kilter_built_gatt_profile(True)])
        rx = rx_of(merged)
        assert rx.min_write_gap_ms == 100 and rx.drop_unpaced


class TestValidation:
    def test_serial_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="no serial"):
            kilter_built(serial="0002")

    def test_api_level_3_is_rejected_and_2_accepted(self) -> None:
        with pytest.raises(ValueError, match="API level 2"):
            kilter_built(api_level=3)
        assert kilter_built(api_level=2).api_level == 2

    def test_strict_pacing_needs_the_kilter_built_controller(self) -> None:
        board = board_for("kilter")
        with pytest.raises(ValueError, match="kilter-built"):
            create_session(board, board.variant_for(None), None, None, None,
                           None, True)

    def test_unknown_controller_is_rejected(self) -> None:
        board = board_for("kilter")
        with pytest.raises(ValueError, match="unknown controller"):
            create_session(board, board.variant_for(None), None, None, None, "x")

    @pytest.mark.parametrize("board_key", ["moonboard", "quantum"])
    def test_other_families_reject_the_controller_options(self, board_key) -> None:
        board = board_for(board_key)
        with pytest.raises(ValueError, match="--controller"):
            create_session(board, board.variant_for(None), None, None, None,
                           config.CONTROLLER_KILTER_BUILT)
        with pytest.raises(ValueError, match="--strict-pacing"):
            create_session(board, board.variant_for(None), None, None, None,
                           None, True)


class TestDecoding:
    def test_16x12_climb_above_led_255_decodes_on_api_2(self) -> None:
        # The reported board: a Kilter 16 x 12 (product size 28, 641 LEDs).
        from protocols.aurora_encoder import encode_climb, hex_to_color_byte

        session = kilter_built(size_id=28)
        led_map = session.geometry.placement_led_map()
        high = sorted({led for led in led_map.values() if led > 255})[:3]
        assert high, "16 x 12 must address LEDs above 255"
        color = hex_to_color_byte(session.geometry.roles[13].led_color)
        for chunk in encode_climb([(led, color) for led in high], api_level=2):
            assert len(chunk) <= config.BLE_CHUNK_SIZE
            session.feed(chunk)
        assert sorted(session.state.get_holds()) == high


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, ms: float) -> None:
        self.now += ms / 1000.0


class TestWriteGuard:
    def guard(self, **kwargs):
        clock = FakeClock()
        flags = kwargs.pop("flags", ("write",))
        return WriteGuard(flags, clock=clock, **kwargs), clock

    def test_paced_writes_pass_quietly(self, caplog) -> None:
        guard, clock = self.guard(min_gap_ms=100)
        with caplog.at_level(logging.WARNING):
            for _ in range(5):
                assert guard.accept(20, "request", "dev")
                clock.advance(100)
        assert guard.unpaced == 0 and not caplog.records

    def test_jitter_within_tolerance_is_paced(self) -> None:
        guard, clock = self.guard(min_gap_ms=100)
        guard.accept(20, "request", "dev")
        clock.advance(80)
        guard.accept(20, "request", "dev")
        assert guard.unpaced == 0

    def test_fast_writes_are_counted_and_reported_once_per_burst(self, caplog) -> None:
        guard, clock = self.guard(min_gap_ms=100)
        with caplog.at_level(logging.WARNING):
            for _ in range(4):
                assert guard.accept(20, "request", "dev")
                clock.advance(5)
            clock.advance(2000)  # next message
            for _ in range(2):
                guard.accept(20, "request", "dev")
                clock.advance(5)
        assert guard.unpaced == 4
        assert sum("expects 100 ms" in r.message for r in caplog.records) == 2

    def test_strict_pacing_drops_the_unpaced_chunk(self) -> None:
        guard, clock = self.guard(min_gap_ms=100, drop_unpaced=True)
        assert guard.accept(20, "request", "dev")
        clock.advance(10)
        assert not guard.accept(20, "request", "dev")
        clock.advance(95)  # 105 ms after the last ACCEPTED chunk
        assert guard.accept(20, "request", "dev")
        assert guard.dropped == 1

    def test_centrals_are_paced_independently(self) -> None:
        guard, clock = self.guard(min_gap_ms=100, drop_unpaced=True)
        assert guard.accept(20, "request", "a")
        clock.advance(10)
        assert guard.accept(20, "request", "b")

    def test_write_command_to_write_only_rx_is_reported_once(self, caplog) -> None:
        guard, _ = self.guard()
        with caplog.at_level(logging.WARNING):
            assert guard.accept(20, "command", "dev")
            assert guard.accept(20, "command", "dev")
        assert sum("without response" in r.message for r in caplog.records) == 1

    def test_classic_box_accepts_commands_without_a_report(self, caplog) -> None:
        guard, _ = self.guard(flags=("write", "write-without-response"))
        with caplog.at_level(logging.WARNING):
            assert guard.accept(20, "command", "dev")
        assert not caplog.records

    def test_no_pacing_check_without_a_gap(self) -> None:
        guard, clock = self.guard()
        for _ in range(3):
            assert guard.accept(20, "request", "dev")
        assert guard.unpaced == 0


def run_main(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "main.py"), *args],
        capture_output=True, text=True, timeout=60, cwd=REPO_ROOT,
    )


class TestCli:
    def test_help_documents_the_controller_options(self) -> None:
        out = run_main("--help").stdout
        assert "--controller" in out and "kilter-built" in out
        assert "--strict-pacing" in out

    def test_kilter_built_with_serial_fails_loudly(self) -> None:
        result = run_main("--controller", "kilter-built", "--serial", "0002",
                          "--headless")
        assert result.returncode == 2
        assert "no serial" in result.stderr

    def test_strict_pacing_alone_fails_loudly(self) -> None:
        result = run_main("--strict-pacing", "--headless")
        assert result.returncode == 2

    def test_moonboard_rejects_the_controller(self) -> None:
        result = run_main("--board", "moonboard", "--controller", "kilter-built",
                          "--headless")
        assert result.returncode == 2
        assert "Aurora-protocol" in result.stderr

    def test_two_kilter_built_boxes_may_share_the_bare_name(self) -> None:
        # Validation passes; without BlueZ the run then stops at the preflight.
        result = run_main("--controller", "kilter-built", "--instances", "2",
                          "--headless")
        assert result.returncode != 2, result.stderr
        assert "advertised as 'Kilter Board'" in result.stderr
