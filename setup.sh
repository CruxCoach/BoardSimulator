#!/usr/bin/env bash
# BoardSimulator — Setup für Debian/Ubuntu/Linux Mint.
# Installiert System-Pakete, legt ein venv an und installiert die Python-Deps.
# BLE-Peripheral-Betrieb braucht danach noch BlueZ-Experimental + root (s. u.).
set -euo pipefail

cd "$(dirname "$0")"

echo "==> System-Pakete (per sudo apt)…"
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y git bluez bluetooth python3-venv python3-tk
else
  echo "!! Kein apt gefunden — bitte git, bluez, bluetooth, python3-venv," >&2
  echo "   python3-tk manuell installieren." >&2
fi

echo "==> Python-venv + Abhängigkeiten…"
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

echo "==> Smoke-Test (kein BLE/Root nötig)…"
venv/bin/python main.py --list >/dev/null && echo "    OK: 'main.py --list' läuft."

cat <<'EOF'

Fertig. Nächste Schritte:

1) BlueZ für BLE-Peripheral aktivieren — in /etc/bluetooth/main.conf unter
   [General] setzen:
       Experimental = true
   danach:  sudo systemctl restart bluetooth

2) Simulator starten (BLE braucht root; venv-Python explizit aufrufen):
       sudo venv/bin/python main.py --board kilter
       sudo venv/bin/python main.py --board moonboard            # 2016 (default)

3) Ohne GUI:        sudo venv/bin/python main.py --board soill --headless
   Alle Optionen:   venv/bin/python main.py --list
EOF
