"""Headless renderer for the MoonBoard: decoded climbs as an ASCII grid.

11 columns (A..K) x grid_rows rows, row 1 at the bottom — matching how a
climber looks at the wall. Lit cells show the role's first letter.
"""

from __future__ import annotations

import logging

from board_state import MoonHoldMap as HoldMap
from protocols.moonboard import COLUMN_LETTERS, NUM_COLUMNS, ROLE_NAMES

logger = logging.getLogger(__name__)


def render_holds_text(holds: HoldMap, grid_rows: int) -> str:
    """Render the lit holds as a multi-line ASCII grid.

    Top row is printed first, row 1 (bottom) last. Lit cells show the
    role's first letter (S/H/F/...), unknown roles a '?'.
    """
    lines: list[str] = []
    header = "    " + " ".join(COLUMN_LETTERS[:NUM_COLUMNS])
    lines.append(header)
    for row in range(grid_rows - 1, -1, -1):
        cells: list[str] = []
        for column in range(NUM_COLUMNS):
            hold = holds.get((column, row))
            if hold is None:
                cells.append(".")
            else:
                role_code = hold[0]
                cells.append(ROLE_NAMES.get(role_code, "?")[0].upper())
        lines.append(f"{row + 1:2d}  " + " ".join(cells))
    return "\n".join(lines)


class MoonHeadlessRenderer:
    """Logs decoded climbs to stdout — the no-GUI board visualization."""

    def __init__(self, grid_rows: int) -> None:
        self._grid_rows = grid_rows

    def update_holds(self, holds: HoldMap) -> None:
        """Print the current lit holds as an ASCII grid + a hold list."""
        if not holds:
            logger.info("Board cleared — no holds lit")
            return
        logger.info("Climb received — %d holds lit:", len(holds))
        for line in render_holds_text(holds, self._grid_rows).splitlines():
            logger.info("  %s", line)
        for (column, row), (role_code, r, g, b) in sorted(holds.items()):
            role = ROLE_NAMES.get(role_code, "?")
            logger.info(
                "    %s%-2d  %-6s  rgb(%3d,%3d,%3d)",
                COLUMN_LETTERS[column], row + 1, role, r, g, b,
            )

    def update_status(self, text: str) -> None:
        logger.info("Status: %s", text)
