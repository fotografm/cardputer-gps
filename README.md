# Cardputer-Adv GPS Passthrough — Setup Playbook

Live NMEA passthrough and satellite signal-strength display for the
**M5Stack Cardputer-Adv** with the **Cap LoRa-1262 HAT** (ATGM336H GNSS chip).

---

## Hardware

| Item | Detail |
|------|--------|
| Host board | M5Stack Cardputer-Adv (ESP32-S3, 8 MB flash, USB HWCDC) |
| HAT | M5Stack Cap LoRa-1262 — SKU U214 |
| LoRa radio | SX1262 |
| GNSS chip | **ATGM336H** (CASIC multi-constellation) |
| Constellations | GPS · GLONASS · Galileo · BeiDou · QZSS |

> **Chip naming note:** M5Stack documentation sometimes refers to this chip
> as "AT6668".  The actual part is an **ATGM336H** from CASIC (中科微电子).
> This distinction matters because the ATGM336H uses the `$PCAS…` command
> set, **not** MediaTek `$PMTK` or Airoha `$PAIR`.  Using the wrong command
> set results in silent failure — the chip ignores every command.

### UART wiring (from the Cap LoRa-1262 schematic)

```
ATGM336H  TX  ──►  ESP32-S3 GPIO 15  (Serial1 RX)
ATGM336H  RX  ◄──  ESP32-S3 GPIO 13  (Serial1 TX)
Baud rate : 115200 8N1
```

> The factory baud rate is **115200**, not the 9600 that generic GNSS
> modules often default to.

---

## Repository layout

```
cardputer-gps/
├── README.md                           ← this file
├── requirements.txt                    ← Python dependencies
├── firmware/
│   └── at6668_passthrough/
│       └── at6668_passthrough.ino      ← Arduino sketch source
├── bin/
│   └── at6668_passthrough.ino.merged.bin  ← pre-compiled flash image
└── tools/
    └── gps_bars_m8030.py               ← satellite signal-bar display
```

---

## One-time setup

### 1 — Install dependencies

**Arduino core** (only needed if recompiling):

```bash
# Install arduino-cli
curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh \
  | BINDIR=~/bin sh

# Add ESP32 board manager URL
~/bin/arduino-cli config init
~/bin/arduino-cli config add board_manager.additional_urls \
  https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json

# Install the ESP32 core (~1 GB download)
~/bin/arduino-cli core update-index
~/bin/arduino-cli core install esp32:esp32
```

**Python tools:**

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**esptool** (for flashing):

```bash
pip install esptool --break-system-packages   # or inside the venv
```

---

### 2 — Enter bootloader mode on the Cardputer-Adv

The Cardputer-Adv must be in ROM bootloader mode before flashing:

1. Turn the **side power switch OFF**
2. Hold the **G0 button** (bottom-left of the keyboard)
3. Connect the **USB-C cable** to your PC
4. Release **G0**

The device will enumerate as `/dev/ttyACM*` or `/dev/ttyUSB*`.

```bash
ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
```

---

### 3 — Flash the passthrough firmware

Use the pre-compiled binary from `bin/`:

```bash
PORT=/dev/ttyACM0   # adjust to your actual port

esptool --chip esp32s3 \
        --port $PORT \
        --baud 460800 \
        write-flash 0x0 \
        bin/at6668_passthrough.ino.merged.bin
```

After flashing, **power-cycle** the device:

1. Disconnect USB-C
2. Flip the side power switch **OFF** then **ON**
3. Reconnect USB-C

> The "Hard resetting via RTS pin" message from esptool does **not**
> reliably boot the Cardputer-Adv into application mode.  A full
> power cycle via the physical switch is required every time.

---

### 4 — Verify NMEA output

```bash
# Quick sanity check — should see $GNRMC, $GNGGA, $GPGSV, $GLGSV, etc.
timeout 10 cat /dev/ttyACM0
```

Expected output (first boot after factory-default GPS flash):

- Only `$GNGGA` and `$GNRMC` on the very first power-on
- After the passthrough sketch runs `PCAS03` + `PCAS00`, GSV/GSA appear
- On subsequent boots all sentence types flow immediately (saved to GPS flash)

---

### 5 — Run the signal-bar display

```bash
cd tools
source ../venv/bin/activate   # if using a venv

python gps_bars_m8030.py --baud 115200 --device /dev/ttyACM0
```

Replace `/dev/ttyACM0` with the actual port shown by `ls /dev/ttyACM*`.

The display shows one colour-coded bar per tracked satellite, grouped by
constellation, with C/N0 (dBHz) on the Y-axis.  Satellites contributing
to the active fix have a bright white top edge; tracked-but-unused
satellites are shown in dimmer colours.

| Bar colour | C/N0 range | Signal quality |
|------------|-----------|----------------|
| Red        | < 10 dBHz | Very weak |
| Orange     | 10–20 dBHz | Weak |
| Yellow     | 20–30 dBHz | Marginal |
| Green      | 30–40 dBHz | Good |
| Cyan       | > 40 dBHz | Excellent |

---

## Recompiling the firmware (optional)

The pre-compiled binary in `bin/` is ready to flash.  If you modify the
sketch, recompile with:

```bash
~/bin/arduino-cli compile \
  --fqbn "esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc" \
  firmware/at6668_passthrough \
  --export-binaries
```

The merged image is written to:
```
firmware/at6668_passthrough/build/esp32.esp32.esp32s3/at6668_passthrough.ino.merged.bin
```

Copy it to `bin/` before committing if you want to keep the binary in sync.

---

## How GSV sentences were unlocked — technical notes

### Why factory defaults lack GSV

The ATGM336H ships configured to output only `$GNGGA` and `$GNRMC`.
Satellite signal data (`$GPGSV`, `$GLGSV`, `$GAGSV`, `$BDGSV`, `$GQGSV`)
requires explicit configuration.

### The CASIC `$PCAS03` command

```
$PCAS03,<GGA>,<GLL>,<GSA>,<GSV>,<RMC>,<VTG>,<ZDA>,<ANT>,<r>,<r>,<r>,<r>,<r>,<r>*CS
```

Each field: `0` = disabled, `1` = every fix, `2–5` = every N-th fix.

The passthrough sketch sends:

```
$PCAS03,1,0,1,1,1,0,0,0,0,0,0,0,0,0*02    ← enable GGA GSA GSV RMC
$PCAS00*01                                  ← save to GPS flash
```

Checksums are computed as XOR of all characters between `$` and `*`.

After `PCAS00` the ATGM336H retains the configuration across power-off,
so the GSV sentences are available even without the ESP32 passthrough
running — though the sketch re-sends the commands on every boot as a
safety net.

### Why PMTK and PAIR commands were silently ignored

During development, `$PMTK314` (MediaTek) and `$PAIR062` (Airoha) commands
were tried first.  The ATGM336H acknowledged neither, because it only
implements the CASIC `$PCAS` protocol.  A brute-force GPIO scan ruled out
a wiring fault — the issue was purely the wrong command vocabulary.

### GPIO 13 wiring confirmed

A brute-force sketch iterated every safe ESP32-S3 GPIO as Serial1 TX,
sending `$PAIR062` and `$PMTK314` on each.  No response was ever received,
which initially suggested the GPS RX line was unconnected.  The schematic
confirmed GPIO 13 was wired correctly; the root cause was the command set
mismatch.  Switching to `$PCAS03` on GPIO 13 unlocked GSV immediately.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `No such file or directory: /dev/ttyACM0` | Wrong port number | `ls /dev/ttyACM*` and use the result |
| Display shows "Waiting for satellites…" | GSV not yet flowing | Wait 5 s after power-on; GSV starts after PCAS init |
| Port present but no data | Partial boot after esptool reset | Power-cycle via physical side switch |
| Binary garbage instead of NMEA | Wrong baud rate | Ensure `--baud 115200` |
| `PMTK` or `PAIR` commands ignored | Wrong command set for ATGM336H | Use `$PCAS03` (CASIC protocol) |
| Device enumerates as `/dev/ttyACM1` (not ACM0) | Previous session left a stale device node | Pass the correct `--device /dev/ttyACM1` |

---

## Licence

MIT — see [LICENSE](LICENSE).
