"""Check that a built mobile resource folder actually contains the offline app.

On Android, extract assets/ from the APK first. On iOS, pass App.app/shared.
Original assets must remain byte-identical, particularly MoonBoard maps.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify(bundle: Path) -> None:
    for name in ("index.html", "style.css", "app.js", "protocols.js", "quantum.js"):
        assert (bundle / name).read_bytes() == (ROOT / "mobile/shared" / name).read_bytes(), name
    catalog = json.loads((bundle / "generated/catalog.json").read_text())
    assert (bundle / "generated/catalog.js").read_bytes() == (ROOT / "mobile/shared/generated/catalog.js").read_bytes()
    for source in (ROOT / "assets").rglob("*"):
        if source.is_file():
            target = bundle / "generated" / source.relative_to(ROOT)
            assert hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(target.read_bytes()).digest(), str(target)
    assert len({item["board"] for item in catalog}) == 8
    print(f"Verified offline scripts, {len(catalog)} configurations and byte-identical original assets")


if __name__ == "__main__":
    verify(Path(sys.argv[1]))
