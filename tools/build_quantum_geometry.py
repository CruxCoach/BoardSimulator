#!/usr/bin/env python3
"""Build the redistribution-safe Quantum diode geometry fixture.

The input is an authorised ewalls routes-delta snapshot.  Only controller
addresses, hold class and coordinates are retained; routes, users, names and
all other catalogue fields are deliberately excluded.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def build(snapshot: Path, output: Path) -> None:
    source = json.loads(snapshot.read_text(encoding="utf-8"))
    diodes = sorted(source["diodes"], key=lambda item: int(item["autocadId"]))
    payload = {
        "schema": 1,
        "source": "ewalls Android 1.44 interoperability snapshot",
        "diodes": [
            {
                "address16": int(item["autocadId"]),
                "address32": int(item["idLedNode"], 16),
                "kind": item["type"],
                "x": round(float(item["x"]), 8),
                "y": round(float(item["y"]), 8),
            }
            for item in diodes
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.snapshot, args.output)


if __name__ == "__main__":
    main()
