"""Hold positions of a Shiny Wall variant, for drawing only.

The controller protocol addresses holds by index, so decoding never needs
positions. The official app keeps no index -> position table (each wall's
hold list lives in the vendor backend), so the simulator has two sources:

- a geometry file in the ``cruxcoach-shinywall-geometry-v1`` format
  (``--shiny-geometry PATH``, or ``data/shinywall_geometry_v1.json`` when
  present): ``x``/``y`` per hold index in grid units, origin bottom left.
  Indices without a position (``x`` null) or without a physical hold
  (``present: false``) exist in the state vector but are not drawn; the
  session warns when an app lights one;
- otherwise a placeholder: index order on the variant's grid, row by row
  from the bottom left. It shows which index lit, not where the hold is.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

GEOMETRY_FORMAT = "cruxcoach-shinywall-geometry-v1"
DEFAULT_GEOMETRY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data",
    "shinywall_geometry_v1.json")


@dataclass(frozen=True)
class ShinyHold:
    """One hold: its index in the state vector and its grid position."""

    index: int
    x: float
    y: float
    name: str | None = None
    kind: str | None = None   # big | kicker | small (geometry file only)


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("format") != GEOMETRY_FORMAT:
        raise ValueError(f"{path}: not a {GEOMETRY_FORMAT} file")
    return data


class ShinyWallGeometry:
    """Positions for one variant; see the module docstring for sources.

    ``hold_count`` (the decoder's vector length) is the override if given,
    else the geometry file's, else the registry's.
    """

    def __init__(self, variant, hold_count: int | None = None,
                 path: str | None = None) -> None:
        self.variant = variant
        entry = None
        if path is not None:
            entry = _load(path).get("variants", {}).get(variant.key)
            if entry is None:
                raise ValueError(f"{path} has no variant '{variant.key}'")
        elif os.path.isfile(DEFAULT_GEOMETRY_PATH):
            entry = _load(DEFAULT_GEOMETRY_PATH).get(
                "variants", {}).get(variant.key)
            path = DEFAULT_GEOMETRY_PATH if entry is not None else None
        file_count = int(entry["hold_count"]) if entry else None
        self.hold_count = hold_count or file_count or variant.hold_count
        if entry is not None:
            self.holds = self._from_file(entry)
            confidence = entry.get("confidence", "unknown")
            self.source = f"{os.path.basename(path)}, confidence {confidence}"
            self.is_placeholder = False
        else:
            self.holds = self._placeholder()
            self.source = (f"placeholder: hold index order on the "
                           f"{variant.columns}x{variant.rows} grid, "
                           "not the real layout")
            self.is_placeholder = True
        self._by_index = {hold.index: hold for hold in self.holds}
        # Vector positions that carry no hold on this wall (send 0 there).
        self.absent = frozenset(i for i in range(self.hold_count)
                                if i not in self._by_index) if entry else frozenset()
        xs = [h.x for h in self.holds] or [0.0]
        ys = [h.y for h in self.holds] or [0.0]
        self.min_x, self.max_x = min(xs), max(xs)
        self.min_y, self.max_y = min(ys), max(ys)

    def _from_file(self, entry: dict) -> list[ShinyHold]:
        holds = []
        for raw in entry.get("holds", []):
            index = int(raw["index"])
            if (not 0 <= index < self.hold_count or raw.get("present") is False
                    or raw.get("x") is None or raw.get("y") is None):
                continue
            holds.append(ShinyHold(index, float(raw["x"]), float(raw["y"]),
                                   raw.get("name"), raw.get("kind")))
        return holds

    def _placeholder(self) -> list[ShinyHold]:
        columns = self.variant.columns
        return [ShinyHold(i, float(i % columns), float(i // columns))
                for i in range(self.hold_count)]

    @property
    def unplaced(self) -> int:
        """Indices of the state vector that have no position."""
        return self.hold_count - len(self._by_index)

    def hold(self, index: int) -> ShinyHold | None:
        return self._by_index.get(index)

    @property
    def span(self) -> tuple[float, float]:
        """Width and height in grid units, with half a cell of margin."""
        return self.max_x - self.min_x + 1.0, self.max_y - self.min_y + 1.0

    @property
    def aspect_ratio(self) -> float:
        width, height = self.span
        return width / height

    def to_unit(self, hold: ShinyHold) -> tuple[float, float]:
        """``(u, v)`` in 0..1, ``v`` measured from the top (screen space)."""
        width, height = self.span
        u = (hold.x - self.min_x + 0.5) / width
        v = 1.0 - (hold.y - self.min_y + 0.5) / height
        return u, v
