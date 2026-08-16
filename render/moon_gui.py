"""Tkinter GUI for visualizing the simulated MoonBoard.

The active variant (:class:`boards.MoonVariant`) controls which board
photo + coord-map ships under ``assets/moonboard/`` and which grid height
the decoder uses. With the bundled assets, the GUI shows the real board
photo with hold overlays placed at the photographed positions. Falls back
to a procedural grid only when the assets or Pillow are not available.

This is the photo/coordinate-map rendering model — unlike the Aurora
family there is no SQLite placement geometry; hold positions come from a
measured per-variant JSON coordinate map.
"""

from __future__ import annotations

import json
import logging
import tkinter as tk
from pathlib import Path
from typing import Callable

from board_state import MoonHoldMap as HoldMap
from boards import Board, MoonVariant
from render.switching import BoardBar
from selection import Selection
from protocols.moonboard import (
    COLUMN_LETTERS,
    NUM_COLUMNS,
    ROLE_COLORS,
    ROLE_NAMES,
)

logger = logging.getLogger(__name__)

# Visual constants — both rendering modes share the colour palette.
BACKGROUND_COLOR: str = "#1a1a1a"
GRID_COLOR: str = "#333333"
EMPTY_HOLD_COLOR: str = "#2c2c2c"
LABEL_COLOR: str = "#888888"
STATUS_FONT: tuple[str, int] = ("Helvetica", 11)
TITLE_FONT: tuple[str, int, str] = ("Helvetica", 13, "bold")
LABEL_FONT: tuple[str, int] = ("Helvetica", 9)

# Image-mode constants — the photo is scaled so the canvas fits a
# comfortable laptop screen while keeping the hold circles legible.
IMAGE_TARGET_HEIGHT: int = 720
RING_WIDTH_IMAGE: int = 4
HOLD_RADIUS_IMAGE: int = 16

# Procedural-fallback constants.
CELL_SIZE: int = 40
MARGIN: int = 44
HOLD_RADIUS: int = 15
RING_WIDTH: int = 3


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    """Convert an (r, g, b) triple to a Tkinter '#rrggbb' string."""
    r, g, b = rgb
    return f"#{r:02x}{g:02x}{b:02x}"


def _load_image_assets(board: Board, variant: MoonVariant):
    """Return (PIL.Image, holds list) for the variant or (None, None) when
    the bundled photo / coord-map / Pillow isn't available."""
    base = Path(board.assets_dir) / variant.asset_base
    webp_path = base.with_suffix(".webp")
    json_path = base.with_suffix(".json")
    if not webp_path.exists() or not json_path.exists():
        logger.info(
            "no image assets for %s (looked under %s.{webp,json})",
            variant.key, base,
        )
        return None, None
    try:
        from PIL import Image  # imported lazily so headless still runs without Pillow
    except ImportError:
        logger.warning(
            "Pillow not installed — falling back to the procedural grid. "
            "Install with `pip install -r requirements.txt`."
        )
        return None, None
    image = Image.open(webp_path).convert("RGB")
    with open(json_path) as f:
        layout = json.load(f)
    return image, layout["holds"]


class MoonBoardGUI:
    """Panel that renders the simulated MoonBoard.

    When the variant has bundled assets, lit holds are drawn on top of
    the real board photo at the coord-map positions; otherwise a
    procedural 11×N grid is drawn. Builds into ``parent``; the controller
    (main._run_gui) owns the single persistent Tk root and its main loop.
    """

    def __init__(
        self,
        parent: tk.Misc,
        board: Board,
        variant: MoonVariant,
        selection: Selection | None = None,
        on_switch: Callable[[Selection], None] | None = None,
        multi_connect: bool = False,
        on_connections: Callable[[bool], None] | None = None,
        instance_count: int = 1,
        on_instances: Callable[[int], None] | None = None,
    ) -> None:
        self._parent = parent
        self._board = board
        self._variant = variant
        self._grid_rows = variant.grid_rows
        self._holds: HoldMap = {}
        self._status_text = "Advertising..."

        # Try image mode first; fall back to procedural if anything is missing.
        self._image, holds_json = _load_image_assets(board, variant)
        self._use_image_mode = self._image is not None and holds_json is not None

        self._canvas_w, self._canvas_h = self._compute_canvas_size()

        self._status_var = tk.StringVar(value=self._status_text)
        status_bar = tk.Frame(parent, bg="#111111")
        status_bar.pack(fill=tk.X, side=tk.TOP)
        tk.Label(
            status_bar, textvariable=self._status_var, bg="#111111", fg="#00cc66",
            font=STATUS_FONT, anchor=tk.W, padx=10, pady=5,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        # Variant chip on the status bar — shows which board the phone is
        # currently talking to.
        self._title_var = tk.StringVar(value=variant.display_name)
        tk.Label(
            status_bar, textvariable=self._title_var,
            bg="#111111", fg="#6688cc", font=TITLE_FONT,
            anchor=tk.E, padx=10, pady=5,
        ).pack(side=tk.RIGHT)

        # Live board switcher — the controller rebuilds this panel + BLE on a pick.
        if selection is not None and on_switch is not None:
            BoardBar(parent, selection, on_switch,
                     multi_connect=multi_connect,
                     on_connections=on_connections,
                     instance_count=instance_count,
                     on_instances=on_instances).pack(
                fill=tk.X, side=tk.TOP, pady=(6, 0))

        self._canvas = tk.Canvas(
            parent, width=self._canvas_w, height=self._canvas_h,
            bg=BACKGROUND_COLOR, highlightthickness=0,
        )
        self._canvas.pack()

        # Keyed by (column, row); row 0 is the bottom of the board. Each
        # mode populates this with one canvas oval per hold position.
        self._cells: dict[tuple[int, int], int] = {}
        self._photo = None  # PhotoImage ref to keep the image alive

        if self._use_image_mode:
            self._draw_image_mode(holds_json)
        else:
            self._draw_procedural_grid()

        self._build_legend()
        logger.info("MoonBoard panel built (%dx%d, %s mode)",
                    self._canvas_w, self._canvas_h,
                    "image" if self._use_image_mode else "procedural")

    def _compute_canvas_size(self) -> tuple[int, int]:
        if self._use_image_mode:
            return self._scaled_image_size()
        return (
            NUM_COLUMNS * CELL_SIZE + 2 * MARGIN,
            self._grid_rows * CELL_SIZE + 2 * MARGIN,
        )

    # ── Image-mode rendering ──────────────────────────────────────

    def _scaled_image_size(self) -> tuple[int, int]:
        w, h = self._image.size
        scale = IMAGE_TARGET_HEIGHT / h
        return int(round(w * scale)), int(round(h * scale))

    def _draw_image_mode(self, holds_json: list[dict]) -> None:
        """Place the board photo as background + an oval per hold cell."""
        from PIL import Image, ImageTk

        scaled = self._image.resize(
            (self._canvas_w, self._canvas_h), Image.LANCZOS,
        )
        self._photo = ImageTk.PhotoImage(scaled)
        self._canvas.create_image(0, 0, anchor=tk.NW, image=self._photo)

        # Coord-map -> canvas-pixel positions, indexed by (column, row).
        for entry in holds_json:
            hold_id = entry["holdId"]
            zero_based = hold_id - 1
            column = zero_based % NUM_COLUMNS
            row = zero_based // NUM_COLUMNS
            px = entry["x"] * self._canvas_w
            py = entry["y"] * self._canvas_h
            cell_id = self._canvas.create_oval(
                px - HOLD_RADIUS_IMAGE, py - HOLD_RADIUS_IMAGE,
                px + HOLD_RADIUS_IMAGE, py + HOLD_RADIUS_IMAGE,
                outline="", fill="", width=0,
            )
            self._cells[(column, row)] = cell_id
        logger.info(
            "GUI image mode: %s (%dx%d, %d holds)",
            self._variant.display_name, self._canvas_w, self._canvas_h,
            len(self._cells),
        )

    # ── Procedural-fallback rendering ────────────────────────────

    def _cell_center(self, column: int, row: int) -> tuple[float, float]:
        """Pixel centre of a grid cell in procedural mode. Row 0 = bottom."""
        px = MARGIN + column * CELL_SIZE + CELL_SIZE / 2
        py = MARGIN + (self._grid_rows - 1 - row) * CELL_SIZE + CELL_SIZE / 2
        return px, py

    def _draw_procedural_grid(self) -> None:
        # Axis labels: column letters A..K (top + bottom).
        for column in range(NUM_COLUMNS):
            cx, _ = self._cell_center(column, 0)
            for cy in (MARGIN / 2, self._canvas_h - MARGIN / 2):
                self._canvas.create_text(
                    cx, cy, text=COLUMN_LETTERS[column],
                    fill=LABEL_COLOR, font=LABEL_FONT,
                )
        # Axis labels: row numbers (left + right).
        for row in range(self._grid_rows):
            _, cy = self._cell_center(0, row)
            for cx in (MARGIN / 2, self._canvas_w - MARGIN / 2):
                self._canvas.create_text(
                    cx, cy, text=str(row + 1),
                    fill=LABEL_COLOR, font=LABEL_FONT,
                )
        # One empty-hold circle per cell.
        for column in range(NUM_COLUMNS):
            for row in range(self._grid_rows):
                px, py = self._cell_center(column, row)
                cell_id = self._canvas.create_oval(
                    px - HOLD_RADIUS, py - HOLD_RADIUS,
                    px + HOLD_RADIUS, py + HOLD_RADIUS,
                    outline=GRID_COLOR, fill=EMPTY_HOLD_COLOR, width=1,
                )
                self._cells[(column, row)] = cell_id

    # ── Legend / lifecycle / hold updates ────────────────────────

    def _build_legend(self) -> None:
        legend = tk.Frame(self._parent, bg=BACKGROUND_COLOR)
        legend.pack(fill=tk.X, side=tk.BOTTOM, pady=4)
        for role_code, name in ROLE_NAMES.items():
            color = ROLE_COLORS.get(role_code, (255, 255, 255))
            swatch = tk.Frame(legend, bg=BACKGROUND_COLOR)
            swatch.pack(side=tk.LEFT, padx=6)
            tk.Label(swatch, text="  ", bg=_rgb_to_hex(color)).pack(side=tk.LEFT)
            tk.Label(
                swatch, text=name, bg=BACKGROUND_COLOR, fg=LABEL_COLOR,
                font=LABEL_FONT,
            ).pack(side=tk.LEFT)

    def update_holds(self, holds: HoldMap) -> None:
        self._parent.after(0, self._apply_holds, holds)

    def update_status(self, text: str) -> None:
        self._parent.after(0, self._status_var.set, text)

    def _apply_holds(self, holds: HoldMap) -> None:
        self._holds = holds
        try:
            # Reset every cell.
            for cell_id in self._cells.values():
                if self._use_image_mode:
                    self._canvas.itemconfig(cell_id, outline="", fill="", width=0)
                else:
                    self._canvas.itemconfig(
                        cell_id, fill=EMPTY_HOLD_COLOR, outline=GRID_COLOR, width=1,
                    )
            # Fill lit cells.
            for (column, row), (_role_code, r, g, b) in holds.items():
                cell_id = self._cells.get((column, row))
                if cell_id is None:
                    logger.warning("Lit hold off-grid: column=%d row=%d", column, row)
                    continue
                color = _rgb_to_hex((r, g, b))
                if self._use_image_mode:
                    self._canvas.itemconfig(
                        cell_id, outline=color, fill="", width=RING_WIDTH_IMAGE,
                    )
                else:
                    self._canvas.itemconfig(
                        cell_id, fill=color, outline="#ffffff", width=RING_WIDTH,
                    )
        except tk.TclError:
            # Panel torn down mid-update (board switch) — drop this frame.
            return
        logger.debug("GUI updated: %d holds active", len(holds))
