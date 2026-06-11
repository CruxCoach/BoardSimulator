"""Tests for per-board role colour resolution (decoded RGB -> role).

Includes the full wire pipeline per board: catalogue frames string ->
placement->LED map -> BoardPacketEncoder port (with the board's own
placement_roles colours) -> protocol decoder -> role resolution. This is
what happens when CruxCoach sends a climb to the simulator.
"""

import pytest

from board_geometry import BoardGeometry
from boards import board_for
from protocols.aurora_encoder import encode_climb, encode_frames, hex_to_color_byte, parse_frames
from protocols.aurora_decoder import ProtocolDecoder
from role_colors import RoleColorResolver


def geometry(board_key: str, layout_key: str | None = None) -> BoardGeometry:
    board = board_for(board_key)
    variant = board.variant_for(layout_key)
    return BoardGeometry(board, variant, variant.default_size_id)


class TestResolver:
    @pytest.mark.parametrize("api_level", [2, 3])
    @pytest.mark.parametrize("board_key,layout", [
        ("kilter", "original"), ("kilter", "homewall"),
        ("tension", "tb1"), ("tension", "tb2"), ("grasshopper", None),
        ("decoy", None), ("soill", None), ("touchstone", None),
    ])
    def test_all_roles_resolve_distinctly(self, board_key, layout, api_level) -> None:
        """Every role's led_color must survive encode->decode and map back
        to exactly that role — for both API levels."""
        geo = geometry(board_key, layout)
        resolver = RoleColorResolver(geo.roles, api_level=api_level)

        from protocols.aurora_decoder import _decode_color_v2, _decode_color_v3
        from protocols.aurora_encoder import rgb332_to_rgb888, scaled_color_v2

        for role in geo.roles.values():
            color_byte = hex_to_color_byte(role.led_color)
            if api_level < 3:
                r8, g8, b8 = rgb332_to_rgb888(color_byte)
                packed = ((scaled_color_v2(r8, 1.0) << 6)
                          | (scaled_color_v2(g8, 1.0) << 4)
                          | (scaled_color_v2(b8, 1.0) << 2))
                rgb = _decode_color_v2(packed)
            else:
                rgb = _decode_color_v3(color_byte)
            resolved = resolver.resolve(*rgb)
            assert resolved is not None, (role, rgb)
            assert resolved.id == role.id

    def test_soill_magenta_is_middle_not_foot(self) -> None:
        """FF00FF means 'middle' on So iLL but 'foot' everywhere else —
        the resolver must be board-specific."""
        from protocols.aurora_decoder import _decode_color_v3
        magenta = _decode_color_v3(hex_to_color_byte("FF00FF"))

        soill = RoleColorResolver(geometry("soill").roles)
        decoy = RoleColorResolver(geometry("decoy").roles)
        assert soill.resolve(*magenta).name == "middle"
        assert decoy.resolve(*magenta).name == "foot"

    def test_kilter_cyan_is_middle_magenta_is_finish(self) -> None:
        """Kilter's palette differs from the Aurora-family boards:
        cyan=middle (not So iLL foot), magenta=finish (not foot)."""
        from protocols.aurora_decoder import _decode_color_v3
        cyan = _decode_color_v3(hex_to_color_byte("00FFFF"))
        magenta = _decode_color_v3(hex_to_color_byte("FF00FF"))

        kilter = RoleColorResolver(geometry("kilter").roles)
        assert kilter.resolve(*cyan).name == "middle"
        assert kilter.resolve(*magenta).name == "finish"

    def test_unknown_color_resolves_to_none(self) -> None:
        resolver = RoleColorResolver(geometry("decoy").roles)
        assert resolver.resolve(1, 2, 3) is None


class TestFullPipeline:
    """frames string -> LED encode (as CruxCoach does) -> decode -> roles."""

    @pytest.mark.parametrize("board_key,layout,frames", [
        ("kilter", "original", "p1073r12p1074r13p1075r14"),
        ("kilter", "homewall", "p4117r42p4118r43p4119r44"),
        ("tension", "tb1", "p1r1p2r2p3r3"),
        ("tension", "tb2", "p304r5p305r6p306r7"),  # TB2 role ids 5-8
        ("grasshopper", None, "p1r1p2r2p3r4"),
        ("decoy", "dungeon", "p579r1p580r2p581r3"),
        ("soill", None, "p1r1p2r2p3r3"),
        ("touchstone", None, "p1r1p2r2p3r4"),
    ])
    def test_roundtrip_resolves_frames_roles(self, board_key, layout, frames) -> None:
        geo = geometry(board_key, layout)
        led_map = geo.placement_led_map()
        resolver = RoleColorResolver(geo.roles, api_level=3)

        # Encode exactly like CruxCoach's send path: placement -> LED,
        # role -> the board's own placement_roles.led_color.
        holds = parse_frames(frames)
        wire_holds = [
            (led_map[placement], hex_to_color_byte(geo.roles[role].led_color))
            for placement, role in holds
        ]
        chunks = encode_climb(wire_holds, api_level=3)

        received: list[list[tuple[int, int, int, int]]] = []
        decoder = ProtocolDecoder(api_level=3, on_message=received.append)
        for chunk in chunks:
            decoder.feed(chunk)

        assert len(received) == 1
        decoded = received[0]
        assert [pos for pos, *_ in decoded] == [led_map[p] for p, _ in holds]
        # Decoded colours resolve back to the original role ids.
        resolved_roles = [resolver.resolve(r, g, b).id for _, r, g, b in decoded]
        assert resolved_roles == [role for _, role in holds]
        # And every lit LED has drawable coordinates.
        for pos, *_ in decoded:
            assert geo.get_raw_coords(pos) is not None

    def test_frames_encode_parse_roundtrip(self) -> None:
        holds = [(304, 5), (305, 6), (306, 7)]
        assert parse_frames(encode_frames(holds)) == holds
