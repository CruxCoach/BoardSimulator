#!/usr/bin/env python3
"""Build the bundled per-board SQLite databases and board images.

Extracts only the geometry/identity tables the simulator needs from the
official Aurora board databases (one APK extract per brand) into small
per-brand SQLite files under data/, and copies the pre-composited board
background images into assets/.

Expected source layout (a local RE workspace, passed as the only argument):
    <source-root>/extract/<brand>/assets/db.sqlite3
    <source-root>/dist/<brand>/board_<size>[_<layout>].webp

Usage:
    python tools/build_data.py /path/to/source-root

This script is a build-time tool: the repository ships its output
(data/<brand>.sqlite3 + assets/<brand>/*.webp), so end users never run it.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys

BRANDS = ["tension", "grasshopper", "decoy", "soill", "touchstone"]

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Tables copied verbatim (column subset) from the official board DB.
# Everything climb-related is dropped — the simulator only needs the
# physical board model: products, layouts, sizes, sets, holes, LEDs,
# placements and the per-board role/colour table.
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

TABLE_SELECT: dict[str, str] = {
    "products": "SELECT id, name FROM products",
    "layouts": "SELECT id, product_id, name, is_mirrored, is_listed FROM layouts",
    "product_sizes": (
        "SELECT id, product_id, name, edge_left, edge_right,"
        " edge_bottom, edge_top, is_listed FROM product_sizes"
    ),
    "sets": "SELECT id, name FROM sets",
    "product_sizes_layouts_sets": (
        "SELECT id, product_size_id, layout_id, set_id"
        " FROM product_sizes_layouts_sets"
    ),
    "holes": "SELECT id, product_id, name, x, y, mirrored_hole_id FROM holes",
    "leds": "SELECT id, product_size_id, hole_id, position FROM leds",
    "placements": (
        "SELECT id, layout_id, hole_id, set_id, default_placement_role_id"
        " FROM placements"
    ),
    "placement_roles": (
        "SELECT id, product_id, position, name, full_name, led_color,"
        " screen_color FROM placement_roles"
    ),
}


def build_db(src_db: str, dst_db: str) -> None:
    """Copy the geometry tables from src_db into a fresh dst_db."""
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


def copy_images(src_dir: str, dst_dir: str) -> None:
    """Copy the composited board background images for one brand."""
    os.makedirs(dst_dir, exist_ok=True)
    count = 0
    for name in sorted(os.listdir(src_dir)):
        if name.startswith("board_") and name.endswith(".webp"):
            shutil.copy2(os.path.join(src_dir, name), os.path.join(dst_dir, name))
            count += 1
    print(f"    images: {count} copied")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source_root",
        help="Path to the source workspace (containing extract/ and dist/)",
    )
    args = parser.parse_args()

    for brand in BRANDS:
        print(f"== {brand} ==")
        src_db = os.path.join(args.source_root, "extract", brand, "assets", "db.sqlite3")
        if not os.path.isfile(src_db):
            print(f"    ERROR: missing source DB {src_db}", file=sys.stderr)
            return 1
        dst_db = os.path.join(REPO_ROOT, "data", f"{brand}.sqlite3")
        os.makedirs(os.path.dirname(dst_db), exist_ok=True)
        build_db(src_db, dst_db)

        src_img = os.path.join(args.source_root, "dist", brand)
        if os.path.isdir(src_img):
            copy_images(src_img, os.path.join(REPO_ROOT, "assets", brand))
        else:
            print(f"    WARNING: no image dir {src_img}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
