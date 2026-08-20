"""Tests for the Tk-free board-selection model behind the live switcher.

These pin the cascade rules the GUI board bar relies on (board change →
default layout + size, layout change → default size, MoonBoard carries no
size) without needing a display or a Bluetooth adapter.
"""

import selection as sel
from selection import Selection


class TestChoices:
    def test_board_choices_cover_all_boards(self) -> None:
        keys = [k for k, _ in sel.board_choices()]
        assert set(keys) == {"kilter", "tension", "grasshopper", "decoy",
                             "soill", "touchstone", "moonboard", "quantum"}

    def test_layout_choices_for_kilter(self) -> None:
        assert sel.layout_choices("kilter") == [
            ("original", "Kilter Board Original"),
            ("homewall", "Kilter Board Homewall"),
        ]

    def test_size_choices_aurora_nonempty(self) -> None:
        sizes = sel.size_choices("kilter", "original")
        assert sizes and all(isinstance(sid, int) for sid, _ in sizes)
        assert 10 in [sid for sid, _ in sizes]  # 12x12 with kickboard

    def test_size_choices_moonboard_empty(self) -> None:
        assert sel.size_choices("moonboard", "2016") == []
        assert sel.size_choices("quantum", "xl") == []


class TestSizeResolution:
    def test_default_size_id_aurora(self) -> None:
        assert sel.default_size_id("kilter", "original") == 10
        assert sel.default_size_id("kilter", "homewall") == 21

    def test_default_size_id_moonboard_is_none(self) -> None:
        assert sel.default_size_id("moonboard", "2016") is None

    def test_effective_size_uses_default_when_unset(self) -> None:
        assert sel.effective_size_id(Selection("kilter", "original")) == 10

    def test_effective_size_keeps_explicit(self) -> None:
        assert sel.effective_size_id(Selection("kilter", "original", 8)) == 8


class TestCascade:
    def test_change_board_resets_to_default_layout_and_size(self) -> None:
        assert sel.change_board("moonboard") == Selection("moonboard", "2016", None)
        assert sel.change_board("kilter") == Selection("kilter", "original", None)
        # Aurora board with a single layout still resets cleanly.
        assert sel.change_board("touchstone").size_id is None

    def test_change_layout_resets_size(self) -> None:
        assert sel.change_layout("kilter", "homewall") == Selection(
            "kilter", "homewall", None)

    def test_change_size_sets_explicit_size(self) -> None:
        assert sel.change_size("kilter", "original", 8) == Selection(
            "kilter", "original", 8)

    def test_is_aurora(self) -> None:
        assert sel.is_aurora("kilter")
        assert not sel.is_aurora("moonboard") and not sel.is_aurora("quantum")
