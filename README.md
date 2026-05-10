# cardputer-gps

USB-CDC GNSS passthrough and satellite signal-strength display for the
**M5Stack Cardputer-Adv** + **Cap LoRa-1262 HAT** (ATGM336H / CASIC GNSS chip).

## Quick start

```bash
# Flash pre-compiled firmware (bootloader mode first — see playbook)
esptool --chip esp32s3 --port /dev/ttyACM0 --baud 460800 \
        write-flash 0x0 bin/at6668_passthrough.ino.merged.bin

# Install Python deps and run signal bars
pip install -r requirements.txt
python tools/gps_bars_m8030.py --baud 115200 --device /dev/ttyACM0
```

See **[cardputer-gps.md](cardputer-gps.md)** for the full setup playbook,
technical notes, and troubleshooting guide.
