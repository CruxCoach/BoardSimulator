#!/usr/bin/env python3
"""Build the redistribution-safe, model-specific Quantum geometry fixture.

The input is the public Quantum SQLite snapshot produced by
cruxcoach-blossom-sync. Only controller addresses, hold class and coordinates
are retained; routes, setters, users and all other catalogue fields are never
read or written.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


MODELS = ("xl", "l", "m", "s", "belay")


def build(snapshot: Path, output: Path) -> None:
    connection = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    try:
        models = {}
        for model in MODELS:
            rows = connection.execute(
                """SELECT autocad_id, led_node, hold_type, x, y
                   FROM quantum_diodes WHERE model=?
                   ORDER BY CAST(autocad_id AS INTEGER), diode_uuid""",
                (model,),
            ).fetchall()
            if not rows:
                raise ValueError(f"Quantum snapshot has no {model} diodes")
            models[model] = [
                {
                    "address16": int(autocad_id),
                    "address32": int(led_node, 16),
                    "kind": hold_type,
                    "x": round(float(x), 8),
                    "y": round(float(y), 8),
                }
                for autocad_id, led_node, hold_type, x, y in rows
            ]
    finally:
        connection.close()
    payload = {
        "schema": 2,
        "source": "ewalls 2.0.14 authorised public snapshot",
        "models": models,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path, help="public quantum.sqlite3")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.snapshot, args.output)


if __name__ == "__main__":
    main()
