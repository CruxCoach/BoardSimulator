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
                 padding: int = 0) -> tuple[float, float]:
        """Map eWalls coordinates onto its original square image viewport.

        The constants are recovered directly from the eWalls 2.0.14
        ``toSvgX``/``toSvgY`` renderer. ``padding`` remains accepted for API
        compatibility but is intentionally ignored: the image itself owns the
        complete 1000-unit viewport.
        """
        del padding
        return (
            diode.x * 9.321401938851603 / 1000.0 * width,
            (100.0 - diode.y) * 9.29368029739777 / 1000.0 * height,
        )
