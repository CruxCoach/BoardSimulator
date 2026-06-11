"""CLI tests — --list and option validation must work without Bluetooth."""

import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_main(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "main.py"), *args],
        capture_output=True, text=True, timeout=60, cwd=REPO_ROOT,
    )


class TestList:
    def test_list_exits_zero_without_bluetooth(self) -> None:
        result = run_main("--list")
        assert result.returncode == 0

    def test_list_shows_all_boards_and_protocols(self) -> None:
        out = run_main("--list").stdout
        for key in ("kilter", "tension", "grasshopper", "decoy", "soill",
                    "touchstone", "moonboard"):
            assert f"{key}:" in out
        assert "[aurora protocol]" in out
        assert "[moonboard protocol]" in out

    def test_list_shows_kilter_layouts_and_sizes(self) -> None:
        out = run_main("--list").stdout
        assert "--layout original (default)" in out
        assert "--layout homewall" in out
        assert "--size 10 (default)" in out  # 12x12 with kickboard
        assert "--size 21 (default)" in out  # Homewall 10x10

    def test_list_shows_moonboard_variants_without_sizes(self) -> None:
        out = run_main("--list").stdout
        assert "--layout mini-2020: Mini MoonBoard 2020 [11x12 grid" in out
        assert "no --size" in out


class TestValidation:
    def test_unknown_board_rejected_by_argparse(self) -> None:
        result = run_main("--board", "spire", "--list")
        assert result.returncode == 2

    def test_moonboard_with_size_fails_loudly(self) -> None:
        result = run_main("--board", "moonboard", "--size", "10", "--headless")
        assert result.returncode == 2
        assert "Aurora-protocol" in result.stderr

    def test_unknown_layout_fails_loudly(self) -> None:
        result = run_main("--board", "tension", "--layout", "tb9",
                          "--headless")
        assert result.returncode != 0


class TestFailFastWithoutBluetooth:
    def test_headless_run_aborts_with_clear_error(self) -> None:
        # On a box without BlueZ the simulator must exit non-zero with a
        # clear message instead of hanging. On a machine WITH BlueZ this
        # test is skipped (the simulator would start up for real).
        import pytest

        from ble.peripheral import BlueZUnavailableError, preflight_check
        try:
            preflight_check()
        except BlueZUnavailableError:
            pass
        else:
            pytest.skip("BlueZ available — fail-fast path not reachable")

        result = run_main("--board", "kilter", "--headless")
        assert result.returncode == 1
        assert "Bluetooth unavailable" in result.stderr
