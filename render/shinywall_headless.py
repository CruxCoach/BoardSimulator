"""Headless Shiny Wall renderer: an ASCII grid of the lit holds.

Rows are printed top-down; ``.`` is an unlit hold, ``S``/``H``/``F``/``T``
start/hand/foot/top and ``?`` an accepted unknown code. Positions come from
:class:`shinywall_geometry.ShinyWallGeometry` (placeholder: index order).
"""

from __future__ import annotations

import logging

from board_state import ShinyHoldMap
from protocols.shinywall import ROLE_LETTERS, summarize
from shinywall_geometry import ShinyWallGeometry

logger = logging.getLogger(__name__)


def render_holds_text(geometry: ShinyWallGeometry, holds: ShinyHoldMap) -> str:
    """The wall as text, top row first.

    Every distinct height is one line and every distinct horizontal
    position one column, so offset rows (half-cell x) and uneven row
    heights from a geometry file stay readable.
    """
    columns = {x: i for i, x in enumerate(sorted({h.x for h in geometry.holds}))}
    rows = {y: i for i, y in enumerate(sorted({h.y for h in geometry.holds},
                                              reverse=True))}
    grid = [[" "] * len(columns) for _ in rows]
    for hold in geometry.holds:
        code = holds.get(hold.index)
        grid[rows[hold.y]][columns[hold.x]] = (
            "." if not code else ROLE_LETTERS.get(code, "?"))
    return "\n".join("".join(line).rstrip() for line in grid)


class ShinyWallHeadlessRenderer:
    def __init__(self, geometry: ShinyWallGeometry) -> None:
        self._geometry = geometry
        logger.info("Shiny Wall positions: %s", geometry.source)

    def update_holds(self, holds: ShinyHoldMap) -> None:
        if not holds:
            logger.info("Shiny Wall cleared")
            return
        logger.info("Shiny Wall: %d holds lit — %s", len(holds), summarize(holds))
        for line in render_holds_text(self._geometry, holds).splitlines():
            logger.info("  %s", line)

    def update_status(self, text: str) -> None:
        logger.info("Status: %s", text)
