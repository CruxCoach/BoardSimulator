# BoardSimulator — Konsolidierter Software-BLE-Simulator für alle CruxCoach-Boards

Reine Software-Simulation **aller sieben interaktiven Boards** aus
CruxCoach 0.2.0 auf Linux via Bluetooth Low Energy (BLE), in einer
Codebasis und per CLI auswählbar:

| Board | Protokoll | Layouts | Rollen-IDs |
|-------|-----------|---------|------------|
| **Kilter** | Aurora | Original, Homewall | 12–15 / 42–45 |
| **Tension** | Aurora | TB1, TB2 (Mirror), TB2 (Spray) | TB1: 1–4, TB2: 5–8 |
| **Grasshopper** | Aurora | Grasshopper 2020 | 1–4 |
| **Decoy** | Aurora | Dungeon Trainer, Dots | 1–4 |
| **So iLL** | Aurora | Summer 2024 | 1–4 |
| **Touchstone** | Aurora | Winter 2020 | 1–4 |
| **MoonBoard** | NUS/ASCII | 2016, Masters 2017, Masters 2019, Mini 2020 | Token-basiert |

Der PC fungiert als BLE-Peripheral über den lokalen Bluetooth-Adapter
(BlueZ) und akzeptiert Verbindungen von der jeweiligen offiziellen App
oder von CruxCoach. Gesendete Kletter-Frames werden dekodiert und live
visualisiert — wahlweise in einer Tkinter-GUI (Board-Bild bzw.
Board-Foto) oder im Headless-Modus als ASCII-Raster auf stdout.

> **Dieses Repo löst die drei Einzel-Simulatoren ab:**
> `KilterSimulator`, `AuroraSimulator` und `MoonSimulator` sind hierin
> aufgegangen. Ein Simulator-Prozess simuliert weiterhin genau **ein**
> Board. Kilters `get_board_details.py`/`led_position_parser.py` sind
> durch `--list` bzw. `board_geometry.py` ersetzt.

## Voraussetzungen

- **Python** 3.10+
- **Linux** mit BlueZ
- **Bluetooth-Adapter** mit BLE-Unterstützung (nur für Live-BLE;
  `--list` und die Tests laufen ohne)
- **System-Pakete:**
  ```bash
  sudo apt install bluez bluetooth python3-tk
  ```

## Installation

```bash
cd BoardSimulator
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### BlueZ konfigurieren

Der BLE-Peripheral-Modus erfordert den experimentellen Modus von BlueZ
(`/etc/bluetooth/main.conf`):

```ini
[General]
Experimental = true
```

Danach: `sudo systemctl restart bluetooth`

## Nutzung

### Simulator starten

Der BLE-Peripheral-Modus benötigt Root-Rechte und einen Bluetooth-Adapter:

```bash
# Kilter Board Original, 12x12 mit Kickboard, GUI
sudo venv/bin/python main.py --board kilter

# Kilter Homewall 10x10
sudo venv/bin/python main.py --board kilter --layout homewall

# Tension Board 2 (Mirror), 12x12
sudo venv/bin/python main.py --board tension --layout tb2

# MoonBoard Mini 2020
sudo venv/bin/python main.py --board moonboard --layout mini-2020

# So iLL im Headless-Modus
sudo venv/bin/python main.py --board soill --headless

# Alle Boards/Layouts/Sizes anzeigen (kein BLE, kein Root nötig)
python main.py --list
```

| Option | Default | Beschreibung |
|--------|---------|-------------|
| `--board` | `kilter` | `kilter` `tension` `grasshopper` `decoy` `soill` `touchstone` `moonboard` |
| `--layout` | erstes Layout | z. B. Kilter: `original` `homewall`; Tension: `tb1` `tb2` `tb2-spray`; MoonBoard: `2016` `masters-2017` `masters-2019` `2024` `mini-2020` |
| `--size` | Layout-Default | Aurora `product_size_id` (siehe `--list`) — nur Aurora-Boards |
| `--api-level` | `3` | Aurora-Protokoll-Version (`2` oder `3`), Suffix `@N` im BLE-Namen — nur Aurora-Boards |
| `--serial` | `0001` | Seriennummer, Suffix `#serial` im BLE-Namen — nur Aurora-Boards |
| `--headless` | aus | ASCII-Raster auf stdout statt GUI (auch: `BOARDSIM_HEADLESS=1`) |

Aurora-spezifische Optionen auf dem MoonBoard brechen mit einer klaren
Fehlermeldung ab — ebenso ein Start ohne BlueZ/Bluetooth-Adapter
(**Fail-fast** statt stummem Hängen).

Nach dem Start:
1. Der BLE-Peripheral advertised — Aurora-Boards als
   `<Boardname>#<serial>@<apiLevel>` (z. B. `Kilter Board#0001@3`),
   das MoonBoard als bloßes `MoonBoard`
2. Die offizielle App oder CruxCoach kann sich verbinden
3. Gesendete Climbs werden live visualisiert — mit den **board-eigenen**
   Rollen-Farben (Kilter z. B. middle=Cyan/finish=Magenta, So iLL
   middle=Magenta/finish=Weiß/foot=Cyan)

In der MoonBoard-GUI lässt sich die Variante zur Laufzeit per Dropdown
wechseln (Decoder und Board-Zustand werden dabei sauber neu aufgebaut).

### Test-Client ausführen

In einem separaten Terminal (während `main.py` läuft):

```bash
source venv/bin/activate
pip install bleak   # nur für den Mock-Client nötig
python tests/test_ble_mock_client.py kilter      # oder tension, moonboard, ...
```

Der Test-Client scannt mit denselben Namens-Regeln wie CruxCoach,
verbindet sich und sendet einen Beispiel-Climb — bei Aurora-Boards drei
Holds in den Farben des jeweiligen Boards, beim MoonBoard einen
ASCII-Frame wie aus CruxCoachs `MoonBoardFrameEncoder`.

### Unit-Tests

Die Tests benötigen weder Root noch einen Bluetooth-Adapter:

```bash
venv/bin/python -m pytest tests/ -q
```

## Architektur

```
main.py                 CLI → Session → BLE-Peripheral + Renderer
boards.py               Registry: 7 Boards, 2 Protokollfamilien
protocols/
  session.py            Familien-Verdrahtung: GATT-Profil, Decoder, State
  aurora_decoder.py     Aurora-Binärprotokoll (API Level 2 + 3)
  aurora_encoder.py     Referenz-Encoder (CruxCoach BoardPacketEncoder-Port)
  moonboard.py          MoonBoard-ASCII-Frames (NUS)
ble/
  peripheral.py         BlueZ D-Bus Peripheral + Fail-fast-Preflight
  gatt.py               GATT-Profil → D-Bus-Objekte
  advertising.py        Extended-Advertising-HCI-Helfer
render/                 GUI + Headless je Familie
board_geometry.py       LED↔Loch-Koordinaten, Edges, Rollen (SQLite)
board_state.py          Thread-sicherer Board-Zustand je Familie
role_colors.py          Wire-Farbe → board-lokale Rolle
data/<brand>.sqlite3    Getrimmte offizielle Board-DBs
assets/<brand>/         Board-Bilder bzw. -Fotos + Koordinaten-Maps
```

Die BLE-Peripheral-Schicht (BlueZ/D-Bus) ist **geteilt**; pro
Protokollfamilie unterscheiden sich nur GATT-Form/Advertising
(deklaratives `GattProfile`) und der Decoder.

## Board-Identität & Erkennung

### Aurora-Familie (Kilter + 5 Boards)

Alle sechs Boards sprechen dasselbe Aurora-Protokoll — die
Brand-Identität steckt ausschließlich im advertised Namen:

- **Offizielle Brand-Apps** akzeptieren jedes Gerät, dessen Name ihren
  Filter-Substring **enthält** (case-sensitive): `Kilter`, `Tension`,
  `Grasshopper`, `Decoy`, `So iLL`, `Touchstone`. API-Level aus dem
  `@N`-Suffix (Default 2), Seriennummer aus dem `#…`-Suffix.
- **CruxCoach 0.2.0** parst `Name#serial@apiLevel`, normalisiert den
  Namensteil (lowercase, Leerzeichen/Bindestriche entfernt) und matcht
  den Brand-**Präfix**; Kilter ist der Fallback für jeden nicht
  erkannten Aurora-Namen.

| Zweck | UUID |
|-------|------|
| Advertising-/Discovery-Service (leer) | `4488B571-7806-4DF6-BCFF-A2897E4953FF` |
| UART-Service (Datenübertragung) | `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` |
| RX-Characteristic (App schreibt Climbs) | `6E400002-B5A3-F393-E0A9-E50E24DCCA9E` |

**Paketformat:**
```
[0x01] [Datenlänge] [Checksum] [0x02] [Position-Code] [Hold-Daten...] [0x03]
```
API 3: 3 Bytes/Hold (16-Bit-LED-Position + RGB332); API 2: 2 Bytes/Hold
(10-Bit-Position, 2 Bit/Kanal, 18-W-Power-Budget). Checksum:
`(~Summe) & 0xFF`. 20-Byte-BLE-Chunks werden reassembliert; ein leeres
Paket löscht das Board.

### MoonBoard

Ein MoonBoard exponiert **nur** den Nordic-UART-Service und advertised
dessen UUID selbst; der Name ist das bloße `MoonBoard` (Präfix-Match in
App und CruxCoach). Die TX-Characteristic (`6E400003-…`) ist ein
Notify-Stub.

**Frame-Format:** `l#<token><pos>,<token><pos>,...#`, z. B.
`l#S0,P1,E197#`. Tokens (case-insensitive): `S`=Start, `R`/`P`=Hand,
`L`=linke Hand, `M`=Match, `F`=Fuß, `E`=Finish. `<pos>` ist die
0-indizierte serielle Strip-Position auf dem serpentinen-verdrahteten
LED-Strip (gerade Spalten von unten nach oben, ungerade von oben nach
unten; Spaltenhöhe 18 bzw. 12 beim Mini 2020). Auch die
`~`-Config-Varianten (`~D…#` = Aux-LEDs über den Griffen) werden
dekodiert.

### Rollen & Farben (board-lokal!)

| Board | start | middle | finish | foot |
|-------|-------|--------|--------|------|
| Kilter (Original + Homewall) | `00FF00` | `00FFFF` | `FF00FF` | `FFA500` |
| Tension / Grasshopper / Decoy / Touchstone | `00FF00` | `0000FF` | `FF0000` | `FF00FF` |
| So iLL | `00FF00` | `FF00FF` | `FFFFFF` | `00FFFF` |

Der Simulator löst dekodierte Farben über die board-eigene Palette zurück
zur Rolle auf (GUI: Screen-Farbe der Rolle, Headless: Rollen-Buchstabe).
MoonBoard-Rollenfarben kommen aus dem Token (Start=Grün, Hand=Blau,
Finish=Rot, Fuß=Cyan, …).

## Datengrundlage

`data/<brand>.sqlite3` sind getrimmte Kopien der offiziellen
Board-Datenbanken (nur Geometrie/Identität). Die fünf
Aurora-Familien-DBs erzeugt `tools/build_data.py` aus einem lokalen
Quell-Workspace; `data/kilter.sqlite3` erzeugt `tools/trim_kilter_db.py`
aus einer vollen Kilter-DB (beschränkt auf Original + Homewall).
`assets/kilter/board_10.webp` ist aus den beiden Hold-Ebenen
(Bolt-ons + Screw-ons) des KilterSimulator komponiert; die
MoonBoard-Fotos + Koordinaten-Maps stammen aus dem MoonSimulator.
Endnutzer brauchen keines der Skripte.

## Troubleshooting

### „Bluetooth unavailable" beim Start
Der Fail-fast-Preflight hat BlueZ oder den Adapter nicht gefunden:
- BlueZ installiert/gestartet? `systemctl status bluetooth`
- Adapter vorhanden und aktiv? `hciconfig` → `sudo hciconfig hci0 up`
- Auf Maschinen ohne Bluetooth laufen nur `--list` und die Tests.

### App findet das Board nicht
- Läuft der Simulator als Root? (`sudo venv/bin/python main.py …`)
- BlueZ-`Experimental = true` gesetzt und Dienst neu gestartet?
- `hcitool` installiert? (Teil von `bluez` — wird für das
  Advertising-Rewrite benötigt)

### Verbindung bricht nach dem ersten Climb ab
Normal bei manchen Android-Versionen — der Simulator startet das
Advertising automatisch neu; einfach erneut verbinden.

## Hinweis

Echte BLE-Verifikation gegen die offiziellen Apps/CruxCoach erfordert
eine Maschine mit Bluetooth-Adapter und ist manuell durchzuführen; die
Unit-Tests decken Protokoll, Geometrie und Registry ab, ersetzen aber
keinen Gerätetest.
