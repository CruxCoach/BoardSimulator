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
from render.layout import MIN_BOARD_HEIGHT, fit_board, scaled
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

# Image-mode constants — the photo OPENS at a size that fits a comfortable
# laptop screen; the window is resizable and every metric below scales with
# the actual panel size (see render.layout).
IMAGE_TARGET_HEIGHT: int = 720
DUAL_IMAGE_TARGET_HEIGHT: int = 520
RING_WIDTH_IMAGE: int = 4
HOLD_RADIUS_IMAGE: int = 16

# Procedural-fallback constants.
CELL_SIZE: int = 40
MARGIN: int = 44
HOLD_RADIUS: int = 15
RING_WIDTH: int = 3

# Resize handling: cheap resampling while dragging, sharp once it settles.
RESIZE_REFINE_MS: int = 150


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
        board_height: int | None = None,
    ) -> None:
        self._parent = parent
        self._board = board
        self._variant = variant
        self._grid_rows = variant.grid_rows
        self._holds: HoldMap = {}
        self._status_text = "Advertising..."
        self._compact = instance_count == 2
        default_height = (
            DUAL_IMAGE_TARGET_HEIGHT if self._compact else IMAGE_TARGET_HEIGHT)
        self._image_base_height = default_height  # constants' calibration
        self._image_target_height = max(
            MIN_BOARD_HEIGHT, board_height or default_height)
        self._cell_size = 30 if self._compact else CELL_SIZE
        self._margin = 32 if self._compact else MARGIN
        self._hold_radius = 11 if self._compact else HOLD_RADIUS
        self._image_hold_radius = 12 if self._compact else HOLD_RADIUS_IMAGE

        # Try image mode first; fall back to procedural if anything is missing.
        self._image, holds_json = _load_image_assets(board, variant)
        self._use_image_mode = self._image is not None and holds_json is not None

        self._canvas_w, self._canvas_h = self._compute_canvas_size()
        self._offset_x = 0
        self._offset_y = 0
        self._view_w, self._view_h = self._canvas_w, self._canvas_h
        self._refine_job = None
        self._high_quality = False
        # Image mode keeps hold positions as 0..1 fractions of the photo, so
        # they survive any resize; procedural mode redraws its grid instead.
        self._hold_fractions: dict[tuple[int, int], tuple[float, float]] = {}
        self._ring_width_image = RING_WIDTH_IMAGE
        self._ring_width = RING_WIDTH

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
        self._canvas.pack(fill=tk.BOTH, expand=True)

        # Keyed by (column, row); row 0 is the bottom of the board. Each
        # mode populates this with one canvas oval per hold position.
        self._cells: dict[tuple[int, int], int] = {}
        self._photo = None  # PhotoImage ref to keep the image alive

        self._image_item: int | None = None
        if self._use_image_mode:
            self._read_hold_fractions(holds_json)
        self._relayout(fast=False)
        self._canvas.bind("<Configure>", self._on_canvas_configure)

        self._build_legend()
        logger.info("MoonBoard panel built (%dx%d, %s mode, resizable)",
                    self._canvas_w, self._canvas_h,
                    "image" if self._use_image_mode else "procedural")

    def _compute_canvas_size(self) -> tuple[int, int]:
        """Size the panel opens with; the window drives it from then on."""
        if self._use_image_mode:
            return self._scaled_image_size()
        return (
            NUM_COLUMNS * self._cell_size + 2 * self._margin,
            self._grid_rows * self._cell_size + 2 * self._margin,
        )

    # ── Resizing ─────────────────────────────────────────────────

    def _on_canvas_configure(self, event) -> None:
        """Re-fit the board whenever the panel's canvas changes size."""
        if event.width == self._view_w and event.height == self._view_h:
            return
        self._view_w, self._view_h = event.width, event.height
        self._relayout(fast=True)

        if self._refine_job is not None:
            self._canvas.after_cancel(self._refine_job)
        self._refine_job = self._canvas.after(RESIZE_REFINE_MS, self._refine)

    def _refine(self) -> None:
        self._refine_job = None
        if not self._high_quality:
            self._relayout(fast=False)

    def _relayout(self, fast: bool) -> None:
        """Re-fit photo or grid into the current canvas area."""
        avail_w, avail_h = max(self._view_w, 1), max(self._view_h, 1)
        try:
            if self._use_image_mode:
                self._relayout_image(avail_w, avail_h, fast=fast)
            else:
                self._relayout_grid(avail_w, avail_h)
            # Lit holds are held in _holds, so a redraw restores them.
            if self._holds:
                self._apply_holds(self._holds)
        except tk.TclError:
            # Panel torn down mid-resize (board switch) — drop this pass.
            return

    # ── Image-mode rendering ──────────────────────────────────────

    def _scaled_image_size(self) -> tuple[int, int]:
        w, h = self._image.size
        scale = self._image_target_height / h
        return int(round(w * scale)), int(round(h * scale))

    def _read_hold_fractions(self, holds_json: list[dict]) -> None:
        """Store coord-map positions as 0..1 fractions of the photo.

        Fractions are resolution-independent, so the same map places the
        hold circles correctly at any window size.
        """
        for entry in holds_json:
            zero_based = entry["holdId"] - 1
            column = zero_based % NUM_COLUMNS
            row = zero_based // NUM_COLUMNS
            self._hold_fractions[(column, row)] = (entry["x"], entry["y"])
        logger.info(
            "GUI image mode: %s (%d holds)",
            self._variant.display_name, len(self._hold_fractions),
        )

    def _relayout_image(self, avail_w: int, avail_h: int, fast: bool) -> None:
        """Scale the board photo to fit and move the hold ovals with it."""
        from PIL import Image, ImageTk

        src_w, src_h = self._image.size
        board_w, board_h, off_x, off_y = fit_board(
            avail_w, avail_h, src_w / src_h)
        if board_w < 10 or board_h < 10:
            return
        self._canvas_w, self._canvas_h = board_w, board_h
        self._offset_x, self._offset_y = off_x, off_y

        resample = Image.BILINEAR if fast else Image.LANCZOS
        self._photo = ImageTk.PhotoImage(
            self._image.resize((board_w, board_h), resample))
        if self._image_item is None:
            self._image_item = self._canvas.create_image(
                off_x, off_y, anchor=tk.NW, image=self._photo)
            self._canvas.tag_lower(self._image_item)
        else:
            self._canvas.coords(self._image_item, off_x, off_y)
            self._canvas.itemconfig(self._image_item, image=self._photo)
        self._high_quality = not fast

        radius = scaled(self._image_hold_radius, board_h,
                        self._image_base_height, minimum=2.0)
        self._ring_width_image = max(1, round(
            scaled(RING_WIDTH_IMAGE, board_h, self._image_base_height)))
        for key, (fx, fy) in self._hold_fractions.items():
            px = off_x + fx * board_w
            py = off_y + fy * board_h
            cell_id = self._cells.get(key)
            if cell_id is None:
                cell_id = self._canvas.create_oval(
                    0, 0, 0, 0, outline="", fill="", width=0)
                self._cells[key] = cell_id
            self._canvas.coords(
                cell_id, px - radius, py - radius, px + radius, py + radius)

    # ── Procedural-fallback rendering ────────────────────────────

    def _cell_center(self, column: int, row: int) -> tuple[float, float]:
        """Pixel centre of a grid cell in procedural mode. Row 0 = bottom."""
        px = (self._offset_x + self._margin + column * self._cell_size
              + self._cell_size / 2)
        py = (self._offset_y + self._margin
              + (self._grid_rows - 1 - row) * self._cell_size
              + self._cell_size / 2)
        return px, py

    def _label_font(self) -> tuple[str, int]:
        """Axis-label font scaled to the current cell size."""
        size = max(6, round(LABEL_FONT[1] * self._cell_size / CELL_SIZE))
        return (LABEL_FONT[0], size)

    def _relayout_grid(self, avail_w: int, avail_h: int) -> None:
        """Re-scale the procedural grid to the available area and redraw it.

        Cell size follows the smaller of the two constraints so the grid
        stays square-celled, then the whole grid is centered.
        """
        margin_ratio = MARGIN / CELL_SIZE
        cell = min(
            avail_w / (NUM_COLUMNS + 2 * margin_ratio),
            avail_h / (self._grid_rows + 2 * margin_ratio),
        )
        if cell < 6:
            return
        self._cell_size = cell
        self._margin = cell * margin_ratio
        self._hold_radius = max(2.0, cell * (HOLD_RADIUS / CELL_SIZE))
        self._ring_width = max(1, round(cell * (RING_WIDTH / CELL_SIZE)))
        grid_w = NUM_COLUMNS * cell + 2 * self._margin
        grid_h = self._grid_rows * cell + 2 * self._margin
        self._canvas_w, self._canvas_h = grid_w, grid_h
        self._offset_x = (avail_w - grid_w) / 2
        self._offset_y = (avail_h - grid_h) / 2

        # Cheap enough to rebuild wholesale (11 x N cells + axis labels).
        self._canvas.delete("grid")
        self._cells.clear()
        self._draw_procedural_grid()
        self._high_quality = True

    def _draw_procedural_grid(self) -> None:
        # Axis labels: column letters A..K (top + bottom).
        font = self._label_font()
        for column in range(NUM_COLUMNS):
            cx, _ = self._cell_center(column, 0)
            for cy in (self._offset_y + self._margin / 2,
                       self._offset_y + self._canvas_h - self._margin / 2):
                self._canvas.create_text(
                    cx, cy, text=COLUMN_LETTERS[column],
                    fill=LABEL_COLOR, font=font, tags="grid",
                )
        # Axis labels: row numbers (left + right).
        for row in range(self._grid_rows):
            _, cy = self._cell_center(0, row)
            for cx in (self._offset_x + self._margin / 2,
                       self._offset_x + self._canvas_w - self._margin / 2):
                self._canvas.create_text(
                    cx, cy, text=str(row + 1),
                    fill=LABEL_COLOR, font=font, tags="grid",
                )
        # One empty-hold circle per cell.
        for column in range(NUM_COLUMNS):
            for row in range(self._grid_rows):
                px, py = self._cell_center(column, row)
                cell_id = self._canvas.create_oval(
                    px - self._hold_radius, py - self._hold_radius,
                    px + self._hold_radius, py + self._hold_radius,
                    outline=GRID_COLOR, fill=EMPTY_HOLD_COLOR, width=1,
                    tags="grid",
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
                        cell_id, outline=color, fill="",
                        width=self._ring_width_image,
                    )
                else:
                    self._canvas.itemconfig(
                        cell_id, fill=color, outline="#ffffff",
                        width=self._ring_width,
                    )
        except tk.TclError:
            # Panel torn down mid-update (board switch) — drop this frame.
            return
        logger.debug("GUI updated: %d holds active", len(holds))
