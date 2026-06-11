"""Board registry: all seven interactive CruxCoach boards in one table.

Two protocol families share this registry:

- ``aurora`` — Kilter plus the five Aurora-family boards (Tension,
  Grasshopper, Decoy, So iLL, Touchstone). Same Aurora BLE protocol
  (advertising service ``4488B571-…``, Nordic UART write characteristic,
  ``[0x01][len][checksum][0x02][type][holds…][0x03]`` packets); they
  differ only in BLE *identity* (advertised name), layouts / product
  sizes / placement→LED maps, and role colours.
- ``moonboard`` — its own protocol: Nordic UART Service advertised
  directly, plain-ASCII climb frames, photo/coordinate-map rendering.

Everything here is static identity data, RE-verified against the official
brand apps (decompiled `BluetoothServiceKt` / `StdBluetoothService` and the
`led_kit_name_substring` string resource) and CruxCoach 0.2.0's
`BoardBleScanner` / `BoardConstants` / `MoonBoardVariant`.

Aurora role ids are BOARD-LOCAL: Kilter Original uses 12-15, Kilter
Homewall 42-45, the five Aurora-family boards 1-4 (Tension Board 2: 5-8).
Colours come from each board's own `placement_roles` table
(data/<brand>.sqlite3).

Scanner-side name matching (why the display names below work):
- The official brand apps accept any device whose name CONTAINS their
  `led_kit_name_substring` (case-sensitive); the MoonBoard app matches
  the "MoonBoard" name prefix.
- CruxCoach 0.2.0 parses `Name#serial@apiLevel`, lowercases the name part,
  strips spaces/hyphens and matches the brand PREFIX (kilter is the
  fallback for any unrecognised Aurora name); MoonBoard is detected by
  its bare "MoonBoard…" name.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

PROTOCOL_AURORA = "aurora"
PROTOCOL_MOONBOARD = "moonboard"


@dataclass(frozen=True)
class AuroraVariant:
    """One selectable layout of an Aurora-protocol board (a physical
    product generation).

    Mirrors CruxCoach's `BoardConstants.AuroraVariant`. `product_id` is the
    Aurora product the layout's holes/roles belong to — relevant for Tension,
    where TB1 (product 4, roles 1-4) and TB2 (product 5, roles 5-8) live in
    one database, and for Kilter (Original product 1 vs Homewall product 7).
    """

    key: str
    layout_id: int
    product_id: int
    display_name: str
    default_size_id: int
    is_mirrored: bool


@dataclass(frozen=True)
class MoonVariant:
    """One simulated MoonBoard variant.

    Mirrors CruxCoach's `MoonBoardVariant`. `grid_rows` drives the BLE
    serpentine arithmetic (column * grid_rows + row, with even-column
    bottom-up / odd-column top-down). The asset base name resolves both
    ``assets/moonboard/<base>.webp`` (board photo) and ``…/<base>.json``
    (per-hold coordinate map).
    """

    key: str
    display_name: str
    grid_rows: int
    asset_base: str


@dataclass(frozen=True)
class Board:
    """Static identity of one simulated board."""

    key: str
    display_name: str        # also the BLE advertising-name prefix
    protocol: str            # PROTOCOL_AURORA | PROTOCOL_MOONBOARD
    official_filter: str     # official app's name filter (contains/prefix)
    cruxcoach_prefix: str    # CruxCoach's normalised brand prefix
    variants: tuple          # first entry = default layout/variant
    leds_per_hold: int = 1   # Aurora API-level-2 power budget (Kilter: 2)

    @property
    def db_path(self) -> str:
        """Bundled geometry DB (Aurora-protocol boards only)."""
        return os.path.join(DATA_DIR, f"{self.key}.sqlite3")

    @property
    def assets_dir(self) -> str:
        return os.path.join(ASSETS_DIR, self.key)

    @property
    def default_variant(self):
        return self.variants[0]

    def variant_for(self, key: str | None):
        """Resolve a CLI layout key; unknown keys raise (loud, not silent)."""
        if key is None:
            return self.default_variant
        wanted = key.strip().lower()
        for variant in self.variants:
            if variant.key == wanted:
                return variant
        raise ValueError(
            f"unknown layout '{key}' for board '{self.key}'; "
            f"choose from {[v.key for v in self.variants]}"
        )


# Aurora layout ids, product ids and default sizes mirror CruxCoach 0.2.0's
# BoardConstants (KILTER_* constants + AURORA_VARIANTS); MoonBoard variants
# mirror MoonBoardVariant. Single-layout boards get one variant whose
# default size is the board's largest listed product size.
BOARDS: dict[str, Board] = {
    "kilter": Board(
        key="kilter",
        display_name="Kilter Board",
        protocol=PROTOCOL_AURORA,
        official_filter="Kilter",
        cruxcoach_prefix="kilter",
        leds_per_hold=2,  # leds_per_hold resource: Kilter alone has 2
        variants=(
            AuroraVariant("original", layout_id=1, product_id=1,
                          display_name="Kilter Board Original",
                          default_size_id=10, is_mirrored=True),
            AuroraVariant("homewall", layout_id=8, product_id=7,
                          display_name="Kilter Board Homewall",
                          default_size_id=21, is_mirrored=True),
        ),
    ),
    "tension": Board(
        key="tension",
        display_name="Tension Board",
        protocol=PROTOCOL_AURORA,
        official_filter="Tension",
        cruxcoach_prefix="tension",
        variants=(
            AuroraVariant("tb1", layout_id=9, product_id=4,
                          display_name="Tension Board", default_size_id=1,
                          is_mirrored=True),
            AuroraVariant("tb2", layout_id=10, product_id=5,
                          display_name="Tension Board 2 (Mirror)",
                          default_size_id=6, is_mirrored=True),
            AuroraVariant("tb2-spray", layout_id=11, product_id=5,
                          display_name="Tension Board 2 (Spray)",
                          default_size_id=6, is_mirrored=False),
        ),
    ),
    "grasshopper": Board(
        key="grasshopper",
        display_name="Grasshopper Board",
        protocol=PROTOCOL_AURORA,
        official_filter="Grasshopper",
        cruxcoach_prefix="grasshopper",
        variants=(
            AuroraVariant("2020", layout_id=1, product_id=1,
                          display_name="Grasshopper 2020", default_size_id=4,
                          is_mirrored=True),
        ),
    ),
    "decoy": Board(
        key="decoy",
        display_name="Decoy Board",
        protocol=PROTOCOL_AURORA,
        official_filter="Decoy",
        cruxcoach_prefix="decoy",
        variants=(
            AuroraVariant("dungeon", layout_id=2, product_id=1,
                          display_name="Decoy Dungeon Trainer",
                          default_size_id=1, is_mirrored=True),
            AuroraVariant("dots", layout_id=1, product_id=1,
                          display_name="Decoy Dots", default_size_id=1,
                          is_mirrored=True),
        ),
    ),
    "soill": Board(
        key="soill",
        display_name="So iLL Board",
        protocol=PROTOCOL_AURORA,
        official_filter="So iLL",
        cruxcoach_prefix="soill",
        variants=(
            AuroraVariant("summer-2024", layout_id=1, product_id=1,
                          display_name="So iLL Summer 2024",
                          default_size_id=2, is_mirrored=True),
        ),
    ),
    "touchstone": Board(
        key="touchstone",
        display_name="Touchstone Board",
        protocol=PROTOCOL_AURORA,
        official_filter="Touchstone",
        cruxcoach_prefix="touchstone",
        variants=(
            AuroraVariant("winter-2020", layout_id=1, product_id=1,
                          display_name="Touchstone Winter 2020",
                          default_size_id=1, is_mirrored=False),
        ),
    ),
    "moonboard": Board(
        key="moonboard",
        display_name="MoonBoard",
        protocol=PROTOCOL_MOONBOARD,
        official_filter="MoonBoard",
        cruxcoach_prefix="moonboard",
        variants=(
            MoonVariant("2016", "MoonBoard 2016", 18, "moonboard_2016"),
            MoonVariant("masters-2017", "MoonBoard Masters 2017", 18,
                        "moonboard_2017"),
            MoonVariant("masters-2019", "MoonBoard Masters 2019", 18,
                        "moonboard_2019"),
            # Mini physically has 12 rows (vs. 18). The serpentine
            # multiplier is 12; BoardSesh's RE notes flag this as the
            # natural extrapolation of the standard protocol — dynamic
            # capture against a real Mini board still pending.
            MoonVariant("mini-2020", "Mini MoonBoard 2020", 12,
                        "mini_moonboard_2020"),
        ),
    ),
}


def board_for(key: str) -> Board:
    """Resolve a CLI board key; unknown keys raise (loud, not silent)."""
    wanted = key.strip().lower()
    if wanted not in BOARDS:
        raise ValueError(f"unknown board '{key}'; choose from {list(BOARDS)}")
    return BOARDS[wanted]
