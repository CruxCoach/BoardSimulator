"""Tests for the bundled board databases and the geometry layer.

LED counts, edges, role tables and placement->LED fixtures are verified
against the official per-brand board databases (brand-app APK extracts),
cross-checked with CruxCoach 0.2.0's BoardConstants where applicable.
"""

import pytest

from board_geometry import BoardGeometry, list_sizes
from boards import BOARDS, PROTOCOL_AURORA, board_for


def geometry(board_key: str, layout_key: str | None = None,
             size_id: int | None = None) -> BoardGeometry:
    board = board_for(board_key)
    variant = board.variant_for(layout_key)
    return BoardGeometry(board, variant, size_id or variant.default_size_id)


# --- LED maps per board (counts from the official DBs) ---

class TestLedMaps:
    @pytest.mark.parametrize("board_key,layout,size,led_count", [
        ("kilter", "original", 10, 476),   # 12x12 with kickboard
        ("kilter", "homewall", 21, 391),    # Homewall 10x10
        ("tension", "tb1", 1, 389),        # TB1 Full Wall
        ("tension", "tb2", 6, 578),        # TB2 12x12
        ("tension", "tb2-spray", 6, 578),  # same physical wall as tb2
        ("grasshopper", None, 4, 578),     # GrandMaster
        ("decoy", None, 1, 578),           # 12x12
        ("soill", None, 2, 340),           # 12x12
        ("touchstone", None, 1, 648),      # Full Size
    ])
    def test_led_counts(self, board_key, layout, size, led_count) -> None:
        geo = geometry(board_key, layout, size)
        assert len(geo.all_positions()) == led_count

    def test_known_coordinates_touchstone(self) -> None:
        # Touchstone LED 36 sits on hole (-68, 4) (official DB).
        geo = geometry("touchstone")
        assert geo.get_raw_coords(36) == (-68, 4)

    def test_unknown_position_returns_none(self) -> None:
        geo = geometry("touchstone")
        assert geo.get_raw_coords(99999) is None


# --- Edges & pixel transform ---

class TestEdges:
    @pytest.mark.parametrize("board_key,layout,size,edges", [
        ("kilter", "original", 10, (0, 144, 0, 156)),
        ("kilter", "homewall", 21, (-56, 56, 24, 144)),
        ("tension", "tb1", 1, (0, 96, 0, 156)),
        ("tension", "tb2", 6, (-68, 68, 0, 144)),
        ("grasshopper", None, 4, (-68, 68, 0, 144)),
        ("decoy", None, 1, (-68, 68, 0, 144)),
        ("soill", None, 2, (-72, 72, -16, 144)),    # kickboard below y=0
        ("touchstone", None, 1, (-72, 72, -12, 144)),
    ])
    def test_edges_match_official_db(self, board_key, layout, size, edges) -> None:
        geo = geometry(board_key, layout, size)
        s = geo.size
        assert (s.edge_left, s.edge_right, s.edge_bottom, s.edge_top) == edges

    def test_pixel_transform_corners(self) -> None:
        geo = geometry("touchstone")  # edges (-72, 72, -12, 144)
        assert geo.to_pixel(-72, -12, 100, 100) == (0.0, 100.0)   # bottom-left
        assert geo.to_pixel(72, 144, 100, 100) == (100.0, 0.0)    # top-right

    def test_pixel_transform_kickboard_negative_y(self) -> None:
        # So iLL bottom row sits at y=-12 (below the 0 line) and must land
        # inside the canvas, not below it.
        geo = geometry("soill")
        _, py = geo.to_pixel(40, -12, 100, 100)
        assert 0 <= py <= 100


# --- Roles (board-local ids + per-board colours) ---

class TestRoles:
    @pytest.mark.parametrize("board_key,layout", [
        ("tension", "tb1"), ("grasshopper", None), ("decoy", None),
        ("soill", None), ("touchstone", None),
    ])
    def test_role_ids_are_board_local_1_to_4(self, board_key, layout) -> None:
        geo = geometry(board_key, layout)
        assert sorted(geo.roles) == [1, 2, 3, 4]
        assert [geo.roles[i].name for i in (1, 2, 3, 4)] == [
            "start", "middle", "finish", "foot",
        ]

    def test_kilter_original_uses_role_ids_12_to_15(self) -> None:
        geo = geometry("kilter", "original")
        assert sorted(geo.roles) == [12, 13, 14, 15]
        assert [geo.roles[i].name for i in (12, 13, 14, 15)] == [
            "start", "middle", "finish", "foot",
        ]

    def test_kilter_homewall_uses_role_ids_42_to_45(self) -> None:
        geo = geometry("kilter", "homewall")
        assert sorted(geo.roles) == [42, 43, 44, 45]
        assert [geo.roles[i].name for i in (42, 43, 44, 45)] == [
            "start", "middle", "finish", "foot",
        ]

    def test_kilter_palette(self) -> None:
        # Kilter: start=green, middle=CYAN, finish=MAGENTA, foot=ORANGE —
        # same for Original (12-15) and Homewall (42-45).
        for layout in ("original", "homewall"):
            geo = geometry("kilter", layout)
            colors = [geo.roles[i].led_color for i in sorted(geo.roles)]
            assert colors == ["00FF00", "00FFFF", "FF00FF", "FFA500"], layout

    def test_kilter_known_coordinates(self) -> None:
        # Kilter Original 12x12 kickboard: LED 0 sits on hole (140, 4)
        # (bundled DB, cross-checked with the KilterSimulator).
        geo = geometry("kilter", "original", 10)
        assert geo.get_raw_coords(0) == (140, 4)

    def test_kilter_size_product_mismatch_raises(self) -> None:
        # Size 10 is Original/product 1; layout homewall is product 7.
        with pytest.raises(ValueError, match="belongs to product"):
            geometry("kilter", "homewall", 10)

    def test_tension_tb2_uses_role_ids_5_to_8(self) -> None:
        geo = geometry("tension", "tb2")
        assert sorted(geo.roles) == [5, 6, 7, 8]
        assert [geo.roles[i].name for i in (5, 6, 7, 8)] == [
            "start", "middle", "finish", "foot",
        ]

    def test_standard_aurora_palette(self) -> None:
        # Tension/Grasshopper/Decoy/Touchstone: green/blue/red/magenta.
        for board_key in ("tension", "grasshopper", "decoy", "touchstone"):
            geo = geometry(board_key)
            colors = [geo.roles[i].led_color for i in sorted(geo.roles)]
            assert colors == ["00FF00", "0000FF", "FF0000", "FF00FF"], board_key

    def test_soill_palette_differs(self) -> None:
        # So iLL: start=green, middle=MAGENTA, finish=WHITE, foot=CYAN.
        geo = geometry("soill")
        colors = [geo.roles[i].led_color for i in (1, 2, 3, 4)]
        assert colors == ["00FF00", "FF00FF", "FFFFFF", "00FFFF"]


# --- placement -> LED resolution (fixtures from the official DBs) ---

class TestPlacementLedMap:
    @pytest.mark.parametrize("board_key,layout,size,fixtures,total", [
        ("kilter", "original", 10, {1073: 1, 1074: 3, 1075: 5}, 476),
        ("kilter", "homewall", 21, {4117: 57, 4118: 56, 4119: 55}, 391),
        ("tension", "tb1", 1, {1: 74, 2: 354, 3: 59}, 303),
        ("tension", "tb2", 6, {304: 282, 305: 285, 306: 286}, 498),
        ("grasshopper", None, 4, {1: 120, 2: 119, 3: 118}, 466),
        ("decoy", "dungeon", 1, {579: 110, 580: 113, 581: 117}, 458),
        ("soill", None, 2, {1: 7, 2: 6, 3: 9}, 340),
        ("touchstone", None, 1, {1: 36, 2: 37, 3: 38}, 648),
    ])
    def test_placement_resolution(self, board_key, layout, size,
                                  fixtures, total) -> None:
        geo = geometry(board_key, layout, size)
        led_map = geo.placement_led_map()
        assert len(led_map) == total
        for placement_id, led_position in fixtures.items():
            assert led_map[placement_id] == led_position
        # Every resolved LED must be drawable (exist in the position map).
        positions = geo.all_positions()
        assert all(led in positions for led in led_map.values())


# --- Validation & size listing ---

class TestValidation:
    def test_unknown_board_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown board"):
            board_for("spire")

    def test_unknown_layout_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown layout"):
            board_for("tension").variant_for("tb3")

    def test_unknown_size_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown product size"):
            geometry("touchstone", None, 99)

    def test_size_product_mismatch_raises(self) -> None:
        # Size 1 (Full Wall) is TB1/product 4; layout tb2 is product 5.
        with pytest.raises(ValueError, match="belongs to product"):
            geometry("tension", "tb2", 1)

    def test_default_size_listed_for_every_variant(self) -> None:
        for board in BOARDS.values():
            if board.protocol != PROTOCOL_AURORA:
                continue
            for variant in board.variants:
                sizes = list_sizes(board, variant.product_id)
                assert variant.default_size_id in [s.id for s in sizes], (
                    board.key, variant.key)

    def test_grasshopper_unlisted_size_hidden(self) -> None:
        # Grasshopper size 1 (GrandMaster duplicate) is is_listed=0.
        sizes = list_sizes(board_for("grasshopper"))
        assert 1 not in [s.id for s in sizes]
