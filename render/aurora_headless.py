"""Headless renderer: decoded climbs as an ASCII grid on stdout.

The grid axes are the distinct hole x/y coordinates of the active board
size (Aurora boards are 4-unit grids, but spacing varies per brand and
some rows/columns are sparse — indexing the *occurring* coordinates keeps
the raster compact). Lit cells show the role letter resolved from the
board's own colour palette, '?' for non-catalogue colours.
"""

from __future__ import annotations

import logging

from board_geometry import BoardGeometry
from board_state import AuroraHoldMap as HoldMap
from role_colors import RoleColorResolver

logger = logging.getLogger(__name__)

# Role letter per role name (start/middle/finish/foot is the Aurora-family
# convention; foot is lowercase to keep it distinct from finish).
ROLE_LETTERS = {"start": "S", "middle": "M", "finish": "F", "foot": "f"}
UNKNOWN_LETTER = "?"
EMPTY_CELL = "."


def render_holds_text(geometry: BoardGeometry, holds: HoldMap,
                      resolver: RoleColorResolver) -> str:
    """Render the lit holds as a multi-line ASCII grid.

    Top row first, bottom row last — matching how a climber looks at the
    wall. Cells are the board's hole lattice (one column per distinct x).
    """
    positions = geometry.all_positions()
    xs = sorted({x for x, _ in positions.values()})
    ys = sorted({y for _, y in positions.values()})
    col_index = {x: i for i, x in enumerate(xs)}
    row_index = {y: i for i, y in enumerate(ys)}

    grid = [[" "] * len(xs) for _ in ys]
    for x, y in positions.values():
        grid[row_index[y]][col_index[x]] = EMPTY_CELL

    for position, (r, g, b) in holds.items():
        coords = positions.get(position)
        if coords is None:
            logger.warning("LED position %d unknown to this board size", position)
            continue
        role = resolver.resolve(r, g, b)
        letter = ROLE_LETTERS.get(role.name, UNKNOWN_LETTER) if role else UNKNOWN_LETTER
        grid[row_index[coords[1]]][col_index[coords[0]]] = letter

    lines = []
    for i in range(len(ys) - 1, -1, -1):
        lines.append(f"y={ys[i]:>4}  " + " ".join(grid[i]))
    return "\n".join(lines)


class HeadlessRenderer:
    """Logs decoded climbs to stdout — the no-GUI board visualization."""

    def __init__(self, geometry: BoardGeometry, resolver: RoleColorResolver) -> None:
        self._geometry = geometry
        self._resolver = resolver

    def update_holds(self, holds: HoldMap) -> None:
        """Print the current lit holds as an ASCII grid + a hold list."""
        if not holds:
            logger.info("Board cleared — no holds lit")
            return
        logger.info("Climb received — %d holds lit (S=start M=middle F=finish f=foot):",
                    len(holds))
        for line in render_holds_text(self._geometry, holds, self._resolver).splitlines():
            logger.info("  %s", line)
        for position in sorted(holds):
            r, g, b = holds[position]
            role = self._resolver.resolve(r, g, b)
            role_name = role.name if role else "?"
            coords = self._geometry.get_raw_coords(position)
            coords_str = f"({coords[0]:>4},{coords[1]:>4})" if coords else "(?,?)"
            logger.info("    led %3d  %s  %-7s rgb(%3d,%3d,%3d)",
                        position, coords_str, role_name, r, g, b)

    def update_status(self, text: str) -> None:
        logger.info("Status: %s", text)
