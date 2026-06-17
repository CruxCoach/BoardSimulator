"""Tkinter GUI for visualizing the simulated Aurora-family board.

Renders the bundled board background image for the active (size, layout)
with colored ring overlays for lit LEDs, matching the CruxCoach visual
style. Falls back to plain hold dots when no image asset exists.
"""

import logging
import os
import tkinter as tk

from PIL import Image as PILImage, ImageTk

from board_geometry import BoardGeometry
from render.switching import BoardBar, SwitchableWindow
from role_colors import RoleColorResolver
from selection import Selection

logger = logging.getLogger(__name__)

# Drawing constants
CANVAS_HEIGHT: int = 750
BACKGROUND_COLOR: str = "#1a1a1a"
RING_WIDTH: int = 3
HOLD_RADIUS: int = 14
DOT_RADIUS: int = 3
DOT_COLOR: str = "#3a3a3a"
STATUS_FONT: tuple[str, int] = ("Helvetica", 11)
TITLE_FONT: tuple[str, int, str] = ("Helvetica", 13, "bold")


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


class BoardGUI(SwitchableWindow):
    """Tkinter window showing the board with image and colored ring overlays."""

    def __init__(self, geometry: BoardGeometry, ble_name: str,
                 resolver: RoleColorResolver | None = None,
                 title: str | None = None,
                 selection: Selection | None = None) -> None:
        self._geometry = geometry
        self._resolver = resolver
        self._holds: dict[int, tuple[int, int, int]] = {}

        # Compute canvas size from board aspect ratio
        aspect = geometry.size.aspect_ratio
        self._canvas_h = CANVAS_HEIGHT
        self._canvas_w = int(CANVAS_HEIGHT * aspect)

        # Build window
        self._root = tk.Tk()
        self._root.title(
            title
            or f"{geometry.board.display_name} Simulator — "
               f"{geometry.variant.display_name} / {geometry.size.name}"
        )
        self._root.configure(bg=BACKGROUND_COLOR)
        self._root.resizable(False, False)

        # Status bar
        self._status_var = tk.StringVar(value="Advertising...")
        status_bar = tk.Frame(self._root, bg="#111111")
        status_bar.pack(fill=tk.X, side=tk.TOP)
        tk.Label(
            status_bar, textvariable=self._status_var, bg="#111111", fg="#00cc66",
            font=STATUS_FONT, anchor=tk.W, padx=10, pady=5,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(
            status_bar, text=ble_name, bg="#111111", fg="#6688cc",
            font=TITLE_FONT, anchor=tk.E, padx=10, pady=5,
        ).pack(side=tk.RIGHT)

        # Live board switcher — rebuilds the window + BLE on a pick.
        if selection is not None:
            BoardBar(self._root, selection, self.request_switch).pack(
                fill=tk.X, side=tk.TOP)

        # Canvas
        self._canvas = tk.Canvas(
            self._root, width=self._canvas_w, height=self._canvas_h,
            bg=BACKGROUND_COLOR, highlightthickness=0,
        )
        self._canvas.pack()

        # Board background: bundled image, else plain hold dots
        self._photo_ref: ImageTk.PhotoImage | None = None  # prevent GC
        self._load_board_image()

        # Pre-create ring items for all LED positions (hidden initially)
        self._rings: dict[int, int] = {}  # position -> canvas item id
        for pos, (x, y) in geometry.all_positions().items():
            px, py = geometry.to_pixel(x, y, self._canvas_w, self._canvas_h)
            ring_id = self._canvas.create_oval(
                px - HOLD_RADIUS, py - HOLD_RADIUS,
                px + HOLD_RADIUS, py + HOLD_RADIUS,
                outline="", width=RING_WIDTH, state=tk.HIDDEN,
            )
            self._rings[pos] = ring_id

        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _load_board_image(self) -> None:
        """Load the board background image, or draw fallback hold dots."""
        path = board_image_path(self._geometry)
        if path is not None:
            try:
                img = PILImage.open(path).convert("RGBA")
                img = img.resize((self._canvas_w, self._canvas_h), PILImage.LANCZOS)
                self._photo_ref = ImageTk.PhotoImage(img)
                self._canvas.create_image(0, 0, anchor=tk.NW, image=self._photo_ref)
                logger.info("Loaded board image: %s", os.path.basename(path))
                return
            except Exception:
                logger.exception("Failed to load board image: %s", path)

        logger.warning("No board image for size %d / layout %d — drawing dots",
                       self._geometry.size.id, self._geometry.variant.layout_id)
        for x, y in self._geometry.all_positions().values():
            px, py = self._geometry.to_pixel(x, y, self._canvas_w, self._canvas_h)
            self._canvas.create_oval(
                px - DOT_RADIUS, py - DOT_RADIUS, px + DOT_RADIUS, py + DOT_RADIUS,
                fill=DOT_COLOR, outline="",
            )

    def update_holds(self, holds: dict[int, tuple[int, int, int]]) -> None:
        """Schedule a hold update on the GUI thread."""
        self._root.after(0, self._apply_holds, holds)

    def update_status(self, text: str) -> None:
        """Update the status bar text (thread-safe via after())."""
        self._root.after(0, self._status_var.set, text)

    def run(self) -> None:
        """Start the Tkinter main loop (blocks until window is closed)."""
        logger.info("GUI started (%dx%d)", self._canvas_w, self._canvas_h)
        self._root.mainloop()

    def _apply_holds(self, holds: dict[int, tuple[int, int, int]]) -> None:
        """Redraw hold rings on the canvas. Runs in the GUI thread."""
        self._holds = holds

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

        if unknown_positions:
            logger.warning("%d holds at LED positions unknown to this board size",
                           unknown_positions)
        logger.debug("GUI updated: %d holds active", len(holds))

    def _on_close(self) -> None:
        """Window closed by the user → end the main loop with no switch
        request, so main.py stops the BLE peripheral and exits."""
        logger.info("GUI window closed")
        self.switch_request = None
        self._root.quit()
