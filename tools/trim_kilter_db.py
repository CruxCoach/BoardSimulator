#!/usr/bin/env python3
"""Build the bundled Kilter geometry database (data/kilter.sqlite3).

Extracts only the geometry/identity tables the simulator needs from a
full Kilter board database (e.g. the sibling KilterSimulator's
data/db.sqlite3, itself an official-app extract), restricted to the two
Kilter products the simulator exposes:

    product 1 / layout 1 — Kilter Board Original
    product 7 / layout 8 — Kilter Board Homewall

Everything climb-/user-related and every non-Kilter Aurora-era product
(JUUL, BKB, Spire, Tycho, ...) is dropped, mirroring what
tools/build_data.py does for the five Aurora-family brands.

Usage:
    python tools/trim_kilter_db.py /path/to/kilter/db.sqlite3

This script is a build-time tool: the repository ships its output
(data/kilter.sqlite3), so end users never run it.
"""

from __future__ import annotations

import argparse
import os
import sqlite3

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

KILTER_PRODUCT_IDS = (1, 7)
KILTER_LAYOUT_IDS = (1, 8)

# Same DDL as tools/build_data.py — the schema every bundled board DB shares.
TABLE_DDL: dict[str, str] = {
    "products": (
        "CREATE TABLE products ("
        " id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
    ),
    "layouts": (
        "CREATE TABLE layouts ("
        " id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL,"
        " name TEXT NOT NULL, is_mirrored INTEGER NOT NULL,"
        " is_listed INTEGER NOT NULL)"
    ),
    "product_sizes": (
        "CREATE TABLE product_sizes ("
        " id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL,"
        " name TEXT NOT NULL, edge_left INTEGER NOT NULL,"
        " edge_right INTEGER NOT NULL, edge_bottom INTEGER NOT NULL,"
        " edge_top INTEGER NOT NULL, is_listed INTEGER NOT NULL)"
    ),
    "sets": (
        "CREATE TABLE sets ("
        " id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
    ),
    "product_sizes_layouts_sets": (
        "CREATE TABLE product_sizes_layouts_sets ("
        " id INTEGER PRIMARY KEY, product_size_id INTEGER NOT NULL,"
        " layout_id INTEGER NOT NULL, set_id INTEGER NOT NULL)"
    ),
    "holes": (
        "CREATE TABLE holes ("
        " id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL,"
        " name TEXT NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL,"
        " mirrored_hole_id INTEGER)"
    ),
    "leds": (
        "CREATE TABLE leds ("
        " id INTEGER PRIMARY KEY, product_size_id INTEGER NOT NULL,"
        " hole_id INTEGER NOT NULL, position INTEGER NOT NULL,"
        " UNIQUE(product_size_id, position))"
    ),
    "placements": (
        "CREATE TABLE placements ("
        " id INTEGER PRIMARY KEY, layout_id INTEGER NOT NULL,"
        " hole_id INTEGER NOT NULL, set_id INTEGER NOT NULL,"
        " default_placement_role_id INTEGER)"
    ),
    "placement_roles": (
        "CREATE TABLE placement_roles ("
        " id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL,"
        " position INTEGER NOT NULL, name TEXT NOT NULL,"
        " full_name TEXT NOT NULL, led_color TEXT NOT NULL,"
        " screen_color TEXT NOT NULL)"
    ),
}

_PRODUCTS = ",".join(str(p) for p in KILTER_PRODUCT_IDS)
_LAYOUTS = ",".join(str(l) for l in KILTER_LAYOUT_IDS)

# Row selection, restricted to the two Kilter products / layouts.
TABLE_SELECT: dict[str, str] = {
    "products": f"SELECT id, name FROM products WHERE id IN ({_PRODUCTS})",
    "layouts": (
        "SELECT id, product_id, name, is_mirrored, is_listed FROM layouts"
        f" WHERE id IN ({_LAYOUTS})"
    ),
    "product_sizes": (
        "SELECT id, product_id, name, edge_left, edge_right,"
        " edge_bottom, edge_top, is_listed FROM product_sizes"
        f" WHERE product_id IN ({_PRODUCTS})"
    ),
    "sets": (
        "SELECT DISTINCT s.id, s.name FROM sets s"
        " JOIN product_sizes_layouts_sets psls ON psls.set_id = s.id"
        f" WHERE psls.layout_id IN ({_LAYOUTS}) ORDER BY s.id"
    ),
    "product_sizes_layouts_sets": (
        "SELECT id, product_size_id, layout_id, set_id"
        " FROM product_sizes_layouts_sets"
        f" WHERE layout_id IN ({_LAYOUTS})"
    ),
    "holes": (
        "SELECT id, product_id, name, x, y, mirrored_hole_id FROM holes"
        f" WHERE product_id IN ({_PRODUCTS})"
    ),
    "leds": (
        "SELECT l.id, l.product_size_id, l.hole_id, l.position FROM leds l"
        " JOIN product_sizes ps ON l.product_size_id = ps.id"
        f" WHERE ps.product_id IN ({_PRODUCTS})"
    ),
    # Kilter's schema predates the per-placement set_id column the newer
    # brand DBs have; derive it from the placed hold so the bundled DB
    # matches the shared schema.
    "placements": (
        "SELECT p.id, p.layout_id, p.hole_id, h.set_id,"
        " p.default_placement_role_id FROM placements p"
        " JOIN holds h ON h.id = p.hold_id"
        f" WHERE p.layout_id IN ({_LAYOUTS})"
    ),
    "placement_roles": (
        "SELECT id, product_id, position, name, full_name, led_color,"
        f" screen_color FROM placement_roles WHERE product_id IN ({_PRODUCTS})"
    ),
}


def build_db(src_db: str, dst_db: str) -> None:
    """Copy the trimmed Kilter geometry tables from src_db into dst_db."""
    if os.path.exists(dst_db):
        os.remove(dst_db)
    src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    dst = sqlite3.connect(dst_db)
    for table, ddl in TABLE_DDL.items():
        dst.execute(ddl)
        rows = src.execute(TABLE_SELECT[table]).fetchall()
        if rows:
            marks = ",".join("?" * len(rows[0]))
            dst.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)
        print(f"    {table}: {len(rows)} rows")
    dst.commit()
    dst.execute("VACUUM")
    dst.close()
    src.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_db", help="Path to a full Kilter db.sqlite3")
    args = parser.parse_args()

    dst_db = os.path.join(REPO_ROOT, "data", "kilter.sqlite3")
    os.makedirs(os.path.dirname(dst_db), exist_ok=True)
    print("== kilter ==")
    build_db(args.source_db, dst_db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
