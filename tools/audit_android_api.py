"""Inspect installed public SDK class signatures without accessing any device.

Usage: python tools/audit_android_api.py "$ANDROID_HOME"
This records public contracts, not hardware behavior or hidden/platform APIs.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

CLASSES = (
    "android.bluetooth.BluetoothManager",
    "android.bluetooth.BluetoothGattServerCallback",
    "android.bluetooth.le.AdvertisingSetCallback",
    "android.bluetooth.le.AdvertisingSet",
)


def inspect(sdk: Path) -> dict:
    result = {}
    for jar in sorted((sdk / "platforms").glob("android-*/android.jar")):
        result[jar.parent.name] = {
            name: subprocess.run(["javap", "-public", "-classpath", str(jar), name],
                                 check=True, capture_output=True, text=True).stdout.splitlines()
            for name in CLASSES
        }
    if not result:
        raise ValueError("No installed Android public SDK jars")
    return result


if __name__ == "__main__":
    print(json.dumps(inspect(Path(sys.argv[1])), indent=2))
