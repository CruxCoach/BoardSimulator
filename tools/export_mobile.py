"""Export the Linux registry/GATT/geometry contract for both offline mobile apps.

Run from the repository root. Generated resources are build outputs, not a second
source of truth. Original images and MoonBoard maps are copied byte-for-byte.
"""
from __future__ import annotations

import json
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from board_geometry import BoardGeometry, list_sizes
from boards import BOARDS
from protocols.session import create_session
from quantum_geometry import QuantumGeometry
from role_colors import _decoded_rgb


def export(destination: Path) -> list[dict]:
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "assets", destination / "assets", dirs_exist_ok=True)
    result = []
    for board in BOARDS.values():
        for variant in board.variants:
            sizes = list_sizes(board, variant.product_id) if board.protocol == "aurora" else [None]
            for size in sizes:
                session = create_session(board, variant, size.id if size else None, None, None)
                profile = session.gatt_profile
                item = dict(id=f"{board.key}/{variant.key}/{size.id if size else '-'}",
                            board=board.key, boardName=board.display_name,
                            layout=variant.key, layoutName=variant.display_name,
                            size=size.id if size else None, sizeName=size.name if size else "Fixed",
                            defaultSize=getattr(variant, "default_size_id", None),
                            family=board.protocol, name=session.ble_name,
                            advertised=profile.advertised_uuid, services=[])
                for service in profile.services:
                    item["services"].append(dict(uuid=service.uuid, characteristics=[
                        dict(uuid=c.uuid, flags=list(c.flags), write=c.receives_writes,
                             value=list(c.initial_value)) for c in service.characteristics]))
                if size:
                    geometry = session.geometry
                    item.update(aspect=size.aspect_ratio,
                                points={str(p): geometry.to_pixel(x, y, 1, 1)
                                        for p, (x, y) in geometry.all_positions().items()},
                                roles=[dict(**asdict(r), rgb2=_decoded_rgb(r.led_color, 2),
                                            rgb3=_decoded_rgb(r.led_color, 3))
                                       for r in geometry.roles.values()])
                    for filename in (f"board_{size.id}_{variant.layout_id}.webp", f"board_{size.id}.webp"):
                        if (Path(board.assets_dir) / filename).exists():
                            item["image"] = f"assets/{board.key}/{filename}"
                            break
                elif board.protocol == "moonboard":
                    layout = json.loads((Path(board.assets_dir) / (variant.asset_base + ".json")).read_text())
                    item.update(rows=variant.grid_rows, aspect=layout["imageAspect"],
                                image=f"assets/moonboard/{variant.asset_base}.webp",
                                points={str(h["holdId"]): [h["x"], h["y"]] for h in layout["holds"]})
                else:
                    geometry = QuantumGeometry(variant)
                    points = {}
                    for diode in geometry.diodes:
                        # Match the existing Linux renderer's eWalls calibration.
                        points[str(diode.address16)] = geometry.to_pixel(diode, 1, 1)
                        points[str(diode.address32)] = points[str(diode.address16)]
                    item.update(aspect=1, points=points,
                                addresses=[d.address16 for d in geometry.diodes],
                                image=f"assets/quantum/{variant.asset_file}")
                result.append(item)
    (destination / "catalog.json").write_text(json.dumps(result, separators=(",", ":")) + "\n")
    # Script loading works on offline file origins without fetch/CORS exceptions.
    (destination / "catalog.js").write_text("globalThis.CATALOG=" + json.dumps(result, separators=(",", ":")) + ";\n")
    return result


if __name__ == "__main__":
    export(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "mobile/shared/generated")
