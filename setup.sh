#!/usr/bin/env bash
# BoardSimulator — Setup for Debian/Ubuntu/Linux Mint.
# Installs system packages, creates a venv and installs the Python deps.
# BLE peripheral operation additionally needs BlueZ Experimental + root (see below).
set -euo pipefail

cd "$(dirname "$0")"

echo "==> System packages (via sudo apt)…"
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y git bluez bluetooth python3-venv python3-tk
else
  echo "!! No apt found — please install git, bluez, bluetooth, python3-venv," >&2
  echo "   python3-tk manually." >&2
fi

echo "==> Python venv + dependencies…"
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

echo "==> Smoke test (no BLE/root needed)…"
venv/bin/python main.py --list >/dev/null && echo "    OK: 'main.py --list' runs."

cat <<'EOF'

Done. Next steps:

1) Enable BlueZ for BLE peripheral mode — in /etc/bluetooth/main.conf under
   [General] set:
       Experimental = true
   then:  sudo systemctl restart bluetooth

2) Start the simulator (BLE needs root; call the venv Python explicitly):
       sudo venv/bin/python main.py --board kilter
       sudo venv/bin/python main.py --board moonboard            # 2016 (default)

3) Without GUI:     sudo venv/bin/python main.py --board soill --headless
   All options:     venv/bin/python main.py --list
EOF
