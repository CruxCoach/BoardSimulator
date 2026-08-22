"""Tkinter panel for visualizing the simulated Aurora-family board.

Renders the bundled board background image for the active (size, layout)
with colored ring overlays for lit LEDs, matching the CruxCoach visual
style. Falls back to plain hold dots when no image asset exists.

This is a *panel*: it builds its widgets into a parent container owned by
the controller (main._run_gui), which keeps a single persistent Tk root
across board switches. Creating/destroying a fresh tk.Tk() per switch
crashed Tcl ("async handler deleted by the wrong thread").
"""

import logging
import os
import tkinter as tk
from typing import Callable

from PIL import Image as PILImage, ImageTk

from board_geometry import BoardGeometry
from render.layout import MIN_BOARD_HEIGHT, fit_board, scaled
from render.switching import BoardBar
from role_colors import RoleColorResolver
from selection import Selection

logger = logging.getLogger(__name__)

# Drawing constants. The heights are the size the panel OPENS with; the
# window is resizable, so everything below scales with the actual board
# height (see render.layout).
CANVAS_HEIGHT: int = 750
DUAL_CANVAS_HEIGHT: int = 520
BACKGROUND_COLOR: str = "#1a1a1a"
RING_WIDTH: int = 3
HOLD_RADIUS: int = 14
DOT_RADIUS: int = 3
DOT_COLOR: str = "#3a3a3a"
STATUS_FONT: tuple[str, int] = ("Helvetica", 11)
TITLE_FONT: tuple[str, int, str] = ("Helvetica", 13, "bold")

# Resize handling: redraw immediately with a cheap resampling filter while
# the window is being dragged, then re-render sharply once the size settles.
RESIZE_REFINE_MS: int = 150


def board_image_path(geometry: BoardGeometry) -> str | None:
    """Resolve the bundled background image for the active size + layout.

    Naming follows the composited assets: board_<size>_<layout>.webp for
    multi-layout sizes, board_<size>.webp as the size-only fallback.
    """
    assets = geometry.board.assets_dir
    candidates = [
        os.path.join(assets, f"board_{geometry.size.id}_{geometry.variant.layout_id}.webp"),
        os.path.join(assets, f"board_{geometry.size.id}.webp"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


class BoardGUI:
    """Panel showing the board image with colored ring overlays.

    Builds into ``parent`` (a Tk container); the controller owns the root
    and its main loop. ``on_switch`` (with ``selection``) wires the board
    bar — omit both for a bar-less panel.
    """

    def __init__(self, parent: tk.Misc, geometry: BoardGeometry, ble_name: str,
                 resolver: RoleColorResolver | None = None,
                 selection: Selection | None = None,
                 on_switch: Callable[[Selection], None] | None = None,
                 multi_connect: bool = False,
                 on_connections: Callable[[bool], None] | None = None,
                 instance_count: int = 1,
                 on_instances: Callable[[int], None] | None = None,
                 board_height: int | None = None) -> None:
        self._parent = parent
        self._geometry = geometry
        self._resolver = resolver
        self._holds: dict[int, tuple[int, int, int]] = {}

        # Opening size from the board aspect ratio; the window drives it after.
        self._aspect = geometry.size.aspect_ratio
        default_h = (DUAL_CANVAS_HEIGHT
                     if instance_count == 2 else CANVAS_HEIGHT)
        self._base_height = default_h  # what the drawing constants assume
        self._board_h = max(MIN_BOARD_HEIGHT, board_height or default_h)
        self._board_w = int(self._board_h * self._aspect)
        self._offset_x = 0
        self._offset_y = 0
        self._view_w, self._view_h = self._board_w, self._board_h
        self._refine_job = None
        self._high_quality = False

        # Status bar
        self._status_var = tk.StringVar(value="Advertising...")
        status_bar = tk.Frame(parent, bg="#111111")
        status_bar.pack(fill=tk.X, side=tk.TOP)
        tk.Label(
            status_bar, textvariable=self._status_var, bg="#111111", fg="#00cc66",
            font=STATUS_FONT, anchor=tk.W, padx=10, pady=5,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(
            status_bar, text=ble_name, bg="#111111", fg="#6688cc",
            font=TITLE_FONT, anchor=tk.E, padx=10, pady=5,
        ).pack(side=tk.RIGHT)

        # Live board switcher — the controller rebuilds this panel + BLE on a pick.
        if selection is not None and on_switch is not None:
            BoardBar(parent, selection, on_switch,
                     multi_connect=multi_connect,
                     on_connections=on_connections,
                     instance_count=instance_count,
                     on_instances=on_instances).pack(fill=tk.X, side=tk.TOP)

        # Canvas — fills whatever space the window gives the panel
        self._canvas = tk.Canvas(
            parent, width=self._board_w, height=self._board_h,
            bg=BACKGROUND_COLOR, highlightthickness=0,
        )
        self._canvas.pack(fill=tk.BOTH, expand=True)

        # Board background: bundled image (kept at source resolution so every
        # resize re-renders from it), else plain hold dots.
        self._photo_ref: ImageTk.PhotoImage | None = None  # prevent GC
        self._image_item: int | None = None
        self._source_image: PILImage.Image | None = None
        self._dots: dict[int, int] = {}  # position -> canvas item id
        self._load_source_image()

        # Pre-create ring items for all LED positions (hidden initially);
        # _relayout gives them their coordinates.
        self._rings: dict[int, int] = {}  # position -> canvas item id
        for pos in geometry.all_positions():
            ring_id = self._canvas.create_oval(
                0, 0, 0, 0, outline="", width=RING_WIDTH, state=tk.HIDDEN,
            )
            self._rings[pos] = ring_id

        self._relayout(fast=False)
        self._canvas.bind("<Configure>", self._on_canvas_configure)

        logger.info("Aurora panel built (%dx%d, resizable)",
                    self._board_w, self._board_h)

    def _load_source_image(self) -> None:
        """Load the board background at source resolution, or set up dots.

        The image is kept unscaled: every resize re-renders from it, so
        enlarging the window gains detail instead of upscaling a thumbnail.
        """
        path = board_image_path(self._geometry)
        if path is not None:
            try:
                self._source_image = PILImage.open(path).convert("RGBA")
                logger.info("Loaded board image: %s", os.path.basename(path))
                return
            except Exception:
                logger.exception("Failed to load board image: %s", path)

        logger.warning("No board image for size %d / layout %d — drawing dots",
                       self._geometry.size.id, self._geometry.variant.layout_id)
        for pos in self._geometry.all_positions():
            self._dots[pos] = self._canvas.create_oval(
                0, 0, 0, 0, fill=DOT_COLOR, outline="",
            )

    def _on_canvas_configure(self, event) -> None:
        """Re-fit the board whenever the panel's canvas changes size."""
        if event.width == self._view_w and event.height == self._view_h:
            return
        self._view_w, self._view_h = event.width, event.height
        self._relayout(fast=True)

        # Re-render the background sharply once resizing has settled.
        if self._refine_job is not None:
            self._canvas.after_cancel(self._refine_job)
        self._refine_job = self._canvas.after(RESIZE_REFINE_MS, self._refine)

    def _refine(self) -> None:
        self._refine_job = None
        if not self._high_quality:
            self._relayout(fast=False)

    def _relayout(self, fast: bool) -> None:
        """Fit the board into the canvas, keeping its aspect ratio."""
        board_w, board_h, off_x, off_y = fit_board(
            max(self._view_w, 1), max(self._view_h, 1), self._aspect)
        if board_w < 10 or board_h < 10:
            return
        self._board_w, self._board_h = board_w, board_h
        self._offset_x, self._offset_y = off_x, off_y

        try:
            self._redraw_background(fast=fast)
            self._reposition_rings()
        except tk.TclError:
            # Panel torn down mid-resize (board switch) — drop this pass.
            return
        logger.debug("Aurora layout: %dx%d at (%d,%d)",
                     board_w, board_h, off_x, off_y)

    def _redraw_background(self, fast: bool) -> None:
        """Re-render the board image (or reposition the fallback dots)."""
        if self._source_image is None:
            radius = scaled(DOT_RADIUS, self._board_h, self._base_height,
                            minimum=1.0)
            for pos, (x, y) in self._geometry.all_positions().items():
                dot_id = self._dots.get(pos)
                if dot_id is None:
                    continue
                px, py = self._to_canvas(x, y)
                self._canvas.coords(
                    dot_id, px - radius, py - radius, px + radius, py + radius)
            self._high_quality = True
            return

        resample = PILImage.BILINEAR if fast else PILImage.LANCZOS
        scaled_image = self._source_image.resize(
            (self._board_w, self._board_h), resample)
        self._photo_ref = ImageTk.PhotoImage(scaled_image)
        if self._image_item is None:
            self._image_item = self._canvas.create_image(
                self._offset_x, self._offset_y, anchor=tk.NW,
                image=self._photo_ref)
            self._canvas.tag_lower(self._image_item)
        else:
            self._canvas.coords(self._image_item, self._offset_x, self._offset_y)
            self._canvas.itemconfig(self._image_item, image=self._photo_ref)
        self._high_quality = not fast

    def _to_canvas(self, x: int, y: int) -> tuple[float, float]:
        """Board coordinate -> canvas pixel, including the centering offset."""
        px, py = self._geometry.to_pixel(x, y, self._board_w, self._board_h)
        return px + self._offset_x, py + self._offset_y

    def _reposition_rings(self) -> None:
        """Move the LED rings to match the current board size and offset."""
        radius = scaled(HOLD_RADIUS, self._board_h, self._base_height,
                        minimum=2.0)
        width = max(1, round(
            scaled(RING_WIDTH, self._board_h, self._base_height)))
        for pos, (x, y) in self._geometry.all_positions().items():
            ring_id = self._rings.get(pos)
            if ring_id is None:
                continue
            px, py = self._to_canvas(x, y)
            self._canvas.coords(
                ring_id, px - radius, py - radius, px + radius, py + radius)
            self._canvas.itemconfig(ring_id, width=width)

    def update_holds(self, holds: dict[int, tuple[int, int, int]]) -> None:
        """Schedule a hold update on the GUI thread."""
        self._parent.after(0, self._apply_holds, holds)

    def update_status(self, text: str) -> None:
        """Update the status bar text (thread-safe via after())."""
        self._parent.after(0, self._status_var.set, text)

    def _apply_holds(self, holds: dict[int, tuple[int, int, int]]) -> None:
        """Redraw hold rings on the canvas. Runs in the GUI thread."""
        self._holds = holds
        try:
            # Hide all rings first
            for ring_id in self._rings.values():
                self._canvas.itemconfig(ring_id, state=tk.HIDDEN)

            # Show rings for active holds. Display uses the role's screen
            # colour when the wire colour resolves to a board role (matching
            # the official app's rendering), the raw wire RGB otherwise.
            unknown_positions = 0
            for position, (r, g, b) in holds.items():
                ring_id = self._rings.get(position)
                if ring_id is None:
                    unknown_positions += 1
                    continue
                color = f"#{r:02x}{g:02x}{b:02x}"
                if self._resolver is not None:
                    role = self._resolver.resolve(r, g, b)
                    if role is not None:
                        color = f"#{role.screen_color}"
                self._canvas.itemconfig(ring_id, outline=color, state=tk.NORMAL)
        except tk.TclError:
            # Panel was torn down mid-update (board switch) — drop this frame.
            return

        if unknown_positions:
            logger.warning("%d holds at LED positions unknown to this board size",
                           unknown_positions)
        logger.debug("GUI updated: %d holds active", len(holds))
