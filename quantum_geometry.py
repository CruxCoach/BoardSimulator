"""Clean-room Quantum Board geometry based on controller diode metadata."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from boards import DATA_DIR, QuantumVariant


@dataclass(frozen=True)
class QuantumDiode:
    address16: int
    address32: int
    kind: str
    x: float
    y: float


class QuantumGeometry:
    """Address lookup and model-specific eWalls image coordinates."""

    def __init__(self, variant: QuantumVariant) -> None:
        self.variant = variant
        path = os.path.join(DATA_DIR, "quantum_geometry.json")
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("schema") != 2:
            raise ValueError("unsupported Quantum geometry schema")
        model_rows = payload.get("models", {}).get(variant.key)
        if not model_rows:
            raise ValueError(f"missing Quantum geometry for {variant.key}")
        diodes = [QuantumDiode(**item) for item in model_rows]
        self.diodes = tuple(diodes)
        self.by_address16 = {d.address16: d for d in self.diodes}
        self.by_address32 = {d.address32: d for d in self.diodes}
        if len(self.by_address16) != len(self.diodes):
            raise ValueError("duplicate Quantum 16-bit diode address")
        if len(self.by_address32) != len(self.diodes):
            raise ValueError("duplicate Quantum 32-bit diode address")
        self._min_x = min(d.x for d in self.diodes)
        self._max_x = max(d.x for d in self.diodes)
        self._min_y = min(d.y for d in self.diodes)
        self._max_y = max(d.y for d in self.diodes)

    @property
    def aspect_ratio(self) -> float:
        # All five original eWalls board-small assets use a square canvas.
        return 1.0

    def diode(self, address: int) -> QuantumDiode | None:
        return self.by_address16.get(address) or self.by_address32.get(address)

    def to_pixel(self, diode: QuantumDiode, width: int, height: int,
                 padding: int = 0, *, tablet: bool = False,
                 compact_phone: bool = False) -> tuple[float, float]:
        """Pixel-faithful eWalls 2.0.14 ``getBoardDiodePosition`` mapping.

        eWalls calibrates all five models independently; Y is curved on XL/L/M
        and Belay has two local corrections. ``height`` is accepted because
        callers expose a rectangular API, but the original board-small view is
        square and therefore uses ``width`` as its board size.
        """
        del padding
        del height
        calibration = _CALIBRATIONS[self.variant.key]
        pad = _padding(self.variant.key, tablet, compact_phone)
        board = float(width)
        left = board * pad[0]
        right = board * pad[1]
        bottom = board * pad[2]
        top = board * pad[3]
        margin_left = board * pad[4]
        usable_width = board - left - right
        usable_height = board - bottom
        inverted_y = 100.0 - diode.y
        progress = inverted_y / 100.0
        inverse_progress = 1.0 - progress

        curve = (usable_height * calibration[4] * progress *
                 inverse_progress)
        corner = 0.0
        adjust_x = 0.0
        adjust_y = 0.0
        if self.variant.key == "belay":
            if progress <= .25 and (diode.x <= 25.5 or diode.x >= 68.0):
                corner = board * -.006 * inverse_progress ** 2
            edge = _clamp((diode.x - 50.0) / 18.0)
            center = _clamp(1.0 - abs(progress - .5) / .18)
            strength = edge * center * center
            adjust_x = board * -.0065 * strength
            adjust_y = board * -.0153 * strength

        return (
            diode.x * usable_width / calibration[0] + left +
            board * calibration[2] + margin_left + adjust_x,
            inverted_y * usable_height / calibration[1] +
            board * calibration[3] + curve + corner - adjust_y + top,
        )


# resizerX, resizerY, horizontal offset, vertical offset, vertical curve.
_CALIBRATIONS = {
    "xl": (106.1251, 107.6049, .06049128205128205, -.007924358974358975, .0067664),
    "l": (106.1355, 107.4252, .060180256410256414, -.008901794871794872, .0048777),
    "m": (106.1307, 107.4211, .060133589743589747, -.008902307692307692, .0047553),
    "s": (108.9741, 108.7541, .062354102564102565, .0460225641025641, 0.0),
    "belay": (108.5258, 111.4584, .07430256410256411, -.02787641025641026, -.0645251),
}


def _padding(model: str, tablet: bool,
             compact_phone: bool) -> tuple[float, float, float, float, float]:
    if model in {"xl", "l", "m"}:
        return (-.015 if compact_phone and not tablet else 0.0,
                .012, 0.0, 0.0, 0.0)
    if model == "s":
        return (.069, .305, .01, 0.0, 0.0)
    if tablet:
        return (-.08, -.087, -.161, 0.0, -.001)
    return (-.08, -.083, -.16, .5, .003)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
