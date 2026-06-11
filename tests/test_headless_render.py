"""Tests for the headless ASCII renderer."""

from board_geometry import BoardGeometry
from boards import board_for
from render.aurora_headless import render_holds_text
from protocols.aurora_decoder import _decode_color_v3
from protocols.aurora_encoder import hex_to_color_byte
from role_colors import RoleColorResolver


def _geometry(board_key: str) -> BoardGeometry:
    board = board_for(board_key)
    variant = board.variant_for(None)
    return BoardGeometry(board, variant, variant.default_size_id)


def _wire_rgb(geo: BoardGeometry, role_id: int) -> tuple[int, int, int]:
    """The RGB the decoder yields for a role's catalogue colour."""
    return _decode_color_v3(hex_to_color_byte(geo.roles[role_id].led_color))


class TestRenderHoldsText:
    def test_grid_covers_all_lattice_rows(self) -> None:
        geo = _geometry("touchstone")
        text = render_holds_text(geo, {}, RoleColorResolver(geo.roles))
        ys = {y for _, y in geo.all_positions().values()}
        assert len(text.splitlines()) == len(ys)

    def test_role_letters_appear_at_lit_cells(self) -> None:
        geo = _geometry("touchstone")
        resolver = RoleColorResolver(geo.roles)
        holds = {
            36: _wire_rgb(geo, 1),  # start  -> S
            37: _wire_rgb(geo, 2),  # middle -> M
            38: _wire_rgb(geo, 3),  # finish -> F
            39: _wire_rgb(geo, 4),  # foot   -> f
        }
        text = render_holds_text(geo, holds, resolver)
        for letter in ("S", "M", "F", "f"):
            assert letter in text

    def test_unknown_color_renders_question_mark(self) -> None:
        geo = _geometry("touchstone")
        text = render_holds_text(geo, {36: (1, 2, 3)}, RoleColorResolver(geo.roles))
        assert "?" in text

    def test_unknown_led_position_ignored(self) -> None:
        geo = _geometry("touchstone")
        resolver = RoleColorResolver(geo.roles)
        text = render_holds_text(geo, {99999: (0, 255, 0)}, resolver)
        assert "S" not in text

    def test_soill_letters_follow_its_own_palette(self) -> None:
        """On So iLL, white must render as F(inish), cyan as f(oot)."""
        geo = _geometry("soill")
        resolver = RoleColorResolver(geo.roles)
        position = next(iter(geo.all_positions()))
        white = render_holds_text(geo, {position: (255, 255, 255)}, resolver)
        cyan = render_holds_text(geo, {position: (0, 255, 255)}, resolver)
        assert "F" in white
        assert "f" in cyan


# --- MoonBoard headless renderer ---

from protocols.moonboard import ROLE_FINISH, ROLE_HAND, ROLE_START
from render.moon_headless import render_holds_text as moon_render


class TestMoonRenderHoldsText:
    def test_grid_has_header_plus_one_line_per_row(self) -> None:
        text = moon_render({}, 18)
        lines = text.splitlines()
        assert len(lines) == 19  # column header + 18 rows
        assert lines[0].split() == list("ABCDEFGHIJK")

    def test_mini_grid_has_12_rows(self) -> None:
        text = moon_render({}, 12)
        assert len(text.splitlines()) == 13

    def test_role_letters_appear_at_lit_cells(self) -> None:
        holds = {
            (0, 0): (ROLE_START, 0, 255, 0),
            (5, 9): (ROLE_HAND, 0, 0, 255),
            (10, 17): (ROLE_FINISH, 255, 0, 0),
        }
        text = moon_render(holds, 18)
        lines = text.splitlines()
        # Top row printed first: row 18 (index 1), row 1 last.
        assert lines[1].startswith("18") and lines[1].endswith("F")
        assert lines[-1].startswith(" 1  S")
        assert "H" in lines[9]  # row 10

    def test_unknown_role_renders_question_mark(self) -> None:
        text = moon_render({(0, 0): (99, 1, 2, 3)}, 18)
        assert "?" in text
