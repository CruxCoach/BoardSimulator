"""Per-board geometry loaded from the bundled per-brand SQLite databases.

Provides everything the simulator needs to visualize a configured board:

- LED position -> raw hole (x, y) coordinates for the active product size
  (`leds JOIN holes` — the same mapping the physical board controller has).
- Board edge bounds from `product_sizes` for the pixel transform
  (the Climbdex/CruxCoach coordinate convention).
- The board's own placement roles (board-LOCAL ids: 1-4 for the
  Aurora-family boards, 5-8 for Tension Board 2, 12-15 for Kilter
  Original, 42-45 for Kilter Homewall) with their LED/screen colours.
- placement_id -> LED position (`placements JOIN leds ON hole_id`, the
  exact join CruxCoach's getPlacementLedMap uses) for tests and the mock
  client, which start from catalogue frames strings.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from boards import AuroraVariant, Board

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Role:
    """One placement role of the active product (board-local id space)."""

    id: int
    name: str
    full_name: str
    led_color: str     # hex RRGGBB — what goes onto the wire
    screen_color: str  # hex RRGGBB — what the official app renders


@dataclass(frozen=True)
class BoardSize:
    """One product size row (edges in placement coordinates)."""

    id: int
    product_id: int
    name: str
    edge_left: int
    edge_right: int
    edge_bottom: int
    edge_top: int

    @property
    def width(self) -> float:
        return float(self.edge_right - self.edge_left)

    @property
    def height(self) -> float:
        return float(self.edge_top - self.edge_bottom)

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height


def _connect(board: Board) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{board.db_path}?mode=ro", uri=True)


def list_sizes(board: Board, product_id: int | None = None) -> list[BoardSize]:
    """All listed product sizes of a board (optionally one product's)."""
    conn = _connect(board)
    try:
        query = (
            "SELECT id, product_id, name, edge_left, edge_right,"
            " edge_bottom, edge_top FROM product_sizes WHERE is_listed = 1"
        )
        params: tuple = ()
        if product_id is not None:
            query += " AND product_id = ?"
            params = (product_id,)
        rows = conn.execute(query + " ORDER BY id", params).fetchall()
    finally:
        conn.close()
    return [BoardSize(*row) for row in rows]


class BoardGeometry:
    """Geometry + roles of one configured (board, variant, product size)."""

    def __init__(self, board: Board, variant: AuroraVariant, product_size_id: int):
        self.board = board
        self.variant = variant

        conn = _connect(board)
        try:
            row = conn.execute(
                "SELECT id, product_id, name, edge_left, edge_right,"
                " edge_bottom, edge_top FROM product_sizes WHERE id = ?",
                (product_size_id,),
            ).fetchone()
            if row is None:
                raise ValueError(
                    f"unknown product size {product_size_id} for board "
                    f"'{board.key}'; use --list to see valid sizes"
                )
            self.size = BoardSize(*row)
            if self.size.product_id != variant.product_id:
                raise ValueError(
                    f"size {product_size_id} ('{self.size.name}') belongs to "
                    f"product {self.size.product_id}, but layout "
                    f"'{variant.key}' is product {variant.product_id}; "
                    f"use --list to see valid combinations"
                )

            # LED position -> raw hole coordinates (what the controller has).
            self._positions: dict[int, tuple[int, int]] = {
                position: (x, y)
                for position, x, y in conn.execute(
                    "SELECT l.position, h.x, h.y FROM leds l"
                    " JOIN holes h ON l.hole_id = h.id"
                    " WHERE l.product_size_id = ?",
                    (product_size_id,),
                )
            }

            # Board-local roles of the variant's product.
            self.roles: dict[int, Role] = {
                role_id: Role(role_id, name, full_name, led, screen)
                for role_id, name, full_name, led, screen in conn.execute(
                    "SELECT id, name, full_name, led_color, screen_color"
                    " FROM placement_roles WHERE product_id = ?"
                    " ORDER BY position",
                    (variant.product_id,),
                )
            }
        finally:
            conn.close()

        logger.info(
            "Geometry loaded: %s / %s / size %d '%s' — %d LEDs, %d roles, "
            "edges l=%d r=%d b=%d t=%d",
            board.display_name, variant.display_name, self.size.id,
            self.size.name, len(self._positions), len(self.roles),
            self.size.edge_left, self.size.edge_right,
            self.size.edge_bottom, self.size.edge_top,
        )

    # ── LED map ──────────────────────────────────────────────────────────

    def get_raw_coords(self, position: int) -> tuple[int, int] | None:
        """Raw hole (x, y) for an LED position, or None if unknown."""
        return self._positions.get(position)

    def all_positions(self) -> dict[int, tuple[int, int]]:
        """Copy of the full LED position -> (x, y) map."""
        return dict(self._positions)

    def to_pixel(self, x: int, y: int, canvas_w: float,
                 canvas_h: float) -> tuple[float, float]:
        """Raw hole coordinates -> canvas pixels (CruxCoach transform):

          px = (x - edge_left) / (edge_right - edge_left) * canvas_width
          py = canvas_height - (y - edge_bottom) / (edge_top - edge_bottom) * canvas_height
        """
        size = self.size
        px = (x - size.edge_left) / size.width * canvas_w
        py = canvas_h - (y - size.edge_bottom) / size.height * canvas_h
        return px, py

    # ── Placement map (encode side — tests / mock client) ────────────────

    def placement_led_map(self) -> dict[int, int]:
        """placement_id -> LED position for the active layout + size.

        Mirrors CruxCoach's getPlacementLedMap join (placements -> leds via
        hole_id, filtered to the active product size); additionally filtered
        to the active layout, since the bundled DBs hold every layout of the
        brand and placement ids are unique per (layout, hole) anyway.
        """
        conn = _connect(self.board)
        try:
            return {
                placement: led
                for placement, led in conn.execute(
                    "SELECT p.id, l.position FROM placements p"
                    " JOIN leds l ON p.hole_id = l.hole_id"
                    " WHERE p.layout_id = ? AND l.product_size_id = ?",
                    (self.variant.layout_id, self.size.id),
                )
            }
        finally:
            conn.close()
