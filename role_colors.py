"""Resolve decoded RGB values back to the board's own placement roles.

The wire carries only an RGB colour per LED (quantised to RGB332 on API
level 3, 2 bits/channel on API level 2). Each board defines its own role
palette in `placement_roles.led_color` — e.g. So iLL lights middle=magenta,
finish=white, foot=cyan, where every other board uses blue/red/magenta — so
the reverse mapping is built per board (and per API level, since the two
levels quantise differently).
"""

from __future__ import annotations

from board_geometry import Role
from protocols.aurora_encoder import hex_to_color_byte
from protocols.aurora_decoder import _decode_color_v2, _decode_color_v3


def _decoded_rgb(led_color_hex: str, api_level: int) -> tuple[int, int, int] | None:
    """The exact RGB the decoder yields for a role's led_color.

    Follows the real encode path (hex -> RGB332 colour byte, as the official
    apps and CruxCoach send it) and the decoder's colour scaling, so matching
    is exact rather than nearest-neighbour for every catalogue palette.
    """
    color_byte = hex_to_color_byte(led_color_hex)
    if color_byte is None:
        return None
    if api_level < 3:
        # v2 carries the colour in the upper 6 bits of the second byte;
        # reconstruct it from the RGB332 byte at full brightness scale.
        from protocols.aurora_encoder import rgb332_to_rgb888, scaled_color_v2
        r8, g8, b8 = rgb332_to_rgb888(color_byte)
        packed = (
            (scaled_color_v2(r8, 1.0) << 6)
            | (scaled_color_v2(g8, 1.0) << 4)
            | (scaled_color_v2(b8, 1.0) << 2)
        )
        return _decode_color_v2(packed)
    return _decode_color_v3(color_byte)


class RoleColorResolver:
    """Maps decoded (r, g, b) values to the board's role, where possible."""

    def __init__(self, roles: dict[int, Role], api_level: int = 3):
        self._by_rgb: dict[tuple[int, int, int], Role] = {}
        for role in roles.values():
            rgb = _decoded_rgb(role.led_color, api_level)
            if rgb is not None:
                self._by_rgb[rgb] = role

    def resolve(self, r: int, g: int, b: int) -> Role | None:
        """The role whose led_color decodes to exactly (r, g, b), or None.

        None is normal for non-catalogue colours (Kilter-app custom colours
        or arbitrary test frames); callers then display the raw RGB.
        """
        return self._by_rgb.get((r, g, b))
