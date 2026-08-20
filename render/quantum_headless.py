"""Headless Quantum Board renderer."""

from __future__ import annotations

import logging

from board_state import QuantumHoldMap
from quantum_geometry import QuantumGeometry

logger = logging.getLogger(__name__)


def render_holds_text(geometry: QuantumGeometry, holds: QuantumHoldMap,
                      width: int = 48, height: int = 24) -> str:
    grid = [[" "] * width for _ in range(height)]
    roles = {"start": "S", "step": "M", "finish": "F",
             "route": "R", "all": "*"}
    for address, light in holds.items():
        diode = geometry.diode(address)
        if diode is None:
            continue
        x, y = geometry.to_pixel(diode, width - 1, height - 1, padding=0)
        grid[max(0, min(height - 1, round(y)))][
            max(0, min(width - 1, round(x)))] = roles.get(light.role, "?")
    return "\n".join("".join(row) for row in grid)


class QuantumHeadlessRenderer:
    def __init__(self, geometry: QuantumGeometry) -> None:
        self._geometry = geometry

    def update_holds(self, holds: QuantumHoldMap) -> None:
        if not holds:
            logger.info("Quantum board cleared")
            return
        logger.info("Quantum command applied — %d diodes lit", len(holds))
        for line in render_holds_text(self._geometry, holds).splitlines():
            logger.info("  %s", line)

    def update_status(self, text: str) -> None:
        logger.info("Status: %s", text)
