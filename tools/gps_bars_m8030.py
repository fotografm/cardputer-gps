#!/usr/bin/env python3
"""
gps_bars_m8030.py
GPS satellite C/N0 signal-strength bar display.

TARGET HARDWARE (primary use case for this repo)
------------------------------------------------
  M5Stack Cardputer-Adv running the at6668_passthrough firmware
  with the Cap LoRa-1262 HAT (ATGM336H GNSS chip, 115200 baud).

    python gps_bars_m8030.py --baud 115200 --device /dev/ttyACM0

  The ATGM336H appears on /dev/ttyACM* (USB-CDC via ESP32-S3 HWCDC).
  Use `ls /dev/ttyACM*` to find the actual port number.

ALSO WORKS WITH
---------------
  - G72 M8130-KT USB GPS dongle   (/dev/ttyUSB0, 9600 baud)
  - VK-162 G-Mouse (u-blox M8030) (/dev/ttyUSB0, 9600 baud)

  python gps_bars_m8030.py                         # defaults to ACM0 @ 9600

BACKGROUND: HOW GSV WAS ENABLED ON THE ATGM336H
------------------------------------------------
  The ATGM336H factory default outputs only $GNGGA and $GNRMC.
  GSV (satellite signal) sentences must be enabled via the CASIC
  $PCAS03 command — NOT the MediaTek $PMTK or Airoha $PAIR commands
  that are commonly documented elsewhere.

  The at6668_passthrough sketch sends these commands on every boot:

    $PCAS03,1,0,1,1,1,0,0,0,0,0,0,0,0,0   enable GGA+GSA+GSV+RMC
    $PCAS00                                  save to GPS flash

  Once PCAS00 has run once the GPS chip retains the config across
  power cycles, so the bars display works even without the passthrough
  sketch active — but the sketch re-sends the commands as a safety net.

REQUIRES
--------
  pip install pyserial pynmea2
"""

import argparse
import serial
import threading
import time
import pynmea2
import tkinter as tk

# ── Defaults ──────────────────────────────────────────────────────────────────

# Cardputer-Adv passthrough enumerates as /dev/ttyACM*; plain USB dongles
# typically appear as /dev/ttyUSB0.  Override with --device.
DEVICE_DEFAULT = '/dev/ttyACM0'

# Cardputer-Adv / ATGM336H runs at 115200; u-blox M8030 dongles run at 9600.
# Pass --baud 115200 when using the Cardputer-Adv.
BAUD_DEFAULT   = 9600

TITLE      = 'GPS Signal Bars'
UPDATE_MS  = 800

# ── Colour palette ────────────────────────────────────────────────────────────

BG          = '#080818'
PANEL       = '#0d0d20'
TEXT        = '#f0f0ff'
MUTED       = '#aaaadd'
HEADING     = '#8888cc'
CLOCK       = '#66dd66'
ALERT       = '#ff6666'

# C/N0 bar colours for satellites contributing to the fix
BAR_COLOURS_USED = [
    (10, '#ff4444'),   # < 10 dBHz — very weak
    (20, '#ff8800'),   # < 20 dBHz — weak
    (30, '#ffdd00'),   # < 30 dBHz — marginal
    (40, '#44cc44'),   # < 40 dBHz — good
    (60, '#00ffaa'),   # ≥ 40 dBHz — excellent
]

# Dimmer variants for satellites tracked but not used in the fix
BAR_COLOURS_UNUSED = [
    (10, '#661111'),
    (20, '#663300'),
    (30, '#665500'),
    (40, '#225522'),
    (60, '#115544'),
]

# One colour per constellation (talker ID)
CONSTELLATION_COLOUR = {
    'GP': '#44aaff',   # GPS       — blue
    'GL': '#ff8844',   # GLONASS   — orange
    'GA': '#aa44ff',   # Galileo   — purple
    'GB': '#ff4488',   # BeiDou    — pink  (ATGM336H uses GB)
    'BD': '#ff4488',   # BeiDou    — pink  (some chips use BD)
    'GQ': '#ffff44',   # QZSS      — yellow (ATGM336H outputs GQGSV)
    'GN': '#ffffff',   # multi-constellation
}

CONSTELLATION_LABEL = {
    'GP': 'GPS', 'GL': 'GLO', 'GA': 'GAL',
    'GB': 'BDS', 'BD': 'BDS', 'GQ': 'QZS', 'GN': 'GNS',
}

# ── Shared state (written by reader thread, read by GUI thread) ───────────────

_lock        = threading.Lock()
_satellites  = {}        # prn_str → {el, az, snr, talker, used}
_active_prns = set()
_status = {
    'device':      DEVICE_DEFAULT,
    'connected':   False,
    'error':       '',
    'last_update': '---',
    'sats_used':   0,
    'sats_seen':   0,
}

# GSV sentence accumulator — keyed by talker ID
_gsv_buf   = {}   # talker → {prn: {...}}
_gsv_total = {}   # talker → expected message count for this epoch
_gsv_seq   = {}   # talker → messages received so far this epoch


def _bar_colour(snr, used):
    table = BAR_COLOURS_USED if used else BAR_COLOURS_UNUSED
    for threshold, colour in table:
        if snr < threshold:
            return colour
    return table[-1][1]


# ── NMEA parsing ──────────────────────────────────────────────────────────────

def _parse_gsv(msg):
    """
    Accumulate per-satellite records from a GSV sentence.

    The ATGM336H emits separate GSV sequences per constellation:
      $GPGSV  GPS satellites
      $GLGSV  GLONASS
      $GAGSV  Galileo
      $BDGSV  BeiDou   (note: BD not GB at the GSV level)
      $GQGSV  QZSS

    Each sequence spans 1-N messages; we commit to _satellites only when
    the final message (seq == total) arrives so we never display partial data.
    """
    global _gsv_buf, _gsv_total, _gsv_seq

    talker = msg.talker
    try:
        total = int(msg.num_messages)
        seq   = int(msg.msg_num)
    except (ValueError, AttributeError):
        return

    if talker not in _gsv_buf or seq == 1:
        _gsv_buf[talker]   = {}
        _gsv_total[talker] = total
        _gsv_seq[talker]   = 0

    _gsv_seq[talker] = seq

    for i in range(1, 5):
        try:
            prn = getattr(msg, f'sv_prn_num_{i}', None)
            el  = getattr(msg, f'elevation_deg_{i}', None)
            az  = getattr(msg, f'azimuth_{i}', None)
            snr = getattr(msg, f'snr_{i}', None)
            if prn:
                _gsv_buf[talker][str(prn)] = {
                    'el':     int(el)  if el  else None,
                    'az':     int(az)  if az  else None,
                    'snr':    int(snr) if snr else 0,
                    'talker': talker,
                }
        except (ValueError, TypeError, AttributeError):
            pass

    if seq == total:
        with _lock:
            for prn, data in _gsv_buf[talker].items():
                if prn in _satellites:
                    _satellites[prn].update(data)
                else:
                    _satellites[prn] = dict(data, used=False)
        _gsv_buf[talker] = {}


def _parse_gsa(msg):
    """Extract the active PRN set from a GSA sentence and mark satellites."""
    used = set()
    for i in range(1, 13):
        try:
            prn = getattr(msg, f'sv_{i}', None)
            if prn:
                used.add(str(prn).lstrip('0') or '0')
                used.add(str(prn))
        except AttributeError:
            pass
    with _lock:
        _active_prns.update(used)
        for prn in list(_satellites.keys()):
            _satellites[prn]['used'] = (
                prn in used or prn.lstrip('0') in used
            )


# ── Serial reader thread ──────────────────────────────────────────────────────

def _reader(device, baud):
    """
    Open the serial port and parse NMEA sentences forever.

    Reconnects automatically after errors (e.g. USB unplug/replug).
    For the Cardputer-Adv the baud rate passed here is largely symbolic
    since the ESP32-S3 HWCDC presents as USB-CDC — the host driver
    ignores the line-coding baud value — but pyserial still requires one.
    """
    while True:
        try:
            with _lock:
                _status['device']    = device
                _status['connected'] = False
                _status['error']     = f'Opening {device}…'

            ser = serial.Serial(device, baud, timeout=2)

            with _lock:
                _status['connected'] = True
                _status['error']     = ''

            while True:
                try:
                    raw = ser.readline().decode('ascii', errors='replace').strip()
                    if not raw.startswith('$'):
                        continue
                    msg      = pynmea2.parse(raw)
                    sentence = type(msg).__name__

                    if sentence == 'GSV':
                        _parse_gsv(msg)
                        with _lock:
                            _status['last_update'] = time.strftime('%H:%M:%S')
                            _status['sats_seen']   = len(_satellites)
                            _status['sats_used']   = sum(
                                1 for s in _satellites.values() if s.get('used')
                            )
                    elif sentence == 'GSA':
                        _parse_gsa(msg)

                except pynmea2.ParseError:
                    pass
                except Exception:
                    pass

        except serial.SerialException as e:
            with _lock:
                _status['connected'] = False
                _status['error']     = str(e)
            time.sleep(3)
        except Exception as e:
            with _lock:
                _status['connected'] = False
                _status['error']     = str(e)
            time.sleep(3)


# ── Tkinter GUI ───────────────────────────────────────────────────────────────

BAR_W      = 38
BAR_GAP    = 8
MARGIN_L   = 50
MARGIN_R   = 12
MARGIN_T   = 28
MARGIN_B   = 48
SCALE_MAX  = 60
YAXIS_STEP = 10


class BarsCanvas(tk.Canvas):
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BG, highlightthickness=0, **kw)

    def redraw(self, satellites):
        self.delete('all')
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 10 or h < 10:
            return

        chart_h = h - MARGIN_T - MARGIN_B
        chart_w = w - MARGIN_L - MARGIN_R

        # Sort: used satellites first, then by descending SNR
        sats = sorted(
            satellites.values(),
            key=lambda s: (not s.get('used', False), -(s.get('snr') or 0))
        )

        # Horizontal grid lines and Y-axis labels (dBHz)
        for db in range(0, SCALE_MAX + 1, YAXIS_STEP):
            y = MARGIN_T + chart_h - int(chart_h * db / SCALE_MAX)
            self.create_line(MARGIN_L, y, w - MARGIN_R, y,
                             fill='#1a1a35', width=1)
            self.create_text(MARGIN_L - 6, y, text=str(db),
                             anchor='e', fill=MUTED, font=('monospace', 8))

        self.create_text(10, MARGIN_T + chart_h // 2,
                         text='C/N₀\ndBHz', anchor='center',
                         fill=HEADING, font=('monospace', 8), justify='center')

        if not sats:
            self.create_text(w // 2, h // 2,
                             text='Waiting for satellites…',
                             fill=MUTED, font=('monospace', 13))
            return

        total_w = len(sats) * (BAR_W + BAR_GAP) - BAR_GAP
        x_start = MARGIN_L + max(0, (chart_w - total_w) // 2)

        for i, sat in enumerate(sats):
            prn    = sat.get('prn', '?')
            snr    = sat.get('snr') or 0
            used   = sat.get('used', False)
            talker = sat.get('talker', 'GP')

            x     = x_start + i * (BAR_W + BAR_GAP)
            bar_h = int(chart_h * min(snr, SCALE_MAX) / SCALE_MAX)
            y_top = MARGIN_T + chart_h - bar_h
            y_bot = MARGIN_T + chart_h

            colour  = _bar_colour(snr, used)
            con_col = CONSTELLATION_COLOUR.get(talker, '#aaaaff')

            self.create_rectangle(x, y_top, x + BAR_W, y_bot,
                                   fill=colour, outline='')
            if used and bar_h > 2:
                self.create_line(x, y_top, x + BAR_W, y_top,
                                 fill='#ffffff', width=2)

            if bar_h > 14:
                label_y, anchor = y_top + 4, 'n'
            else:
                label_y, anchor = y_top - 4, 's'
            if snr > 0:
                self.create_text(x + BAR_W // 2, label_y,
                                 text=str(snr), anchor=anchor,
                                 fill=TEXT, font=('monospace', 8, 'bold'))

            self.create_text(x + BAR_W // 2, y_bot + 4,
                             text=prn, anchor='n',
                             fill=TEXT if used else MUTED,
                             font=('monospace', 9, 'bold'))

            self.create_text(x + BAR_W // 2, y_bot + 17,
                             text=CONSTELLATION_LABEL.get(talker, talker),
                             anchor='n', fill=con_col,
                             font=('monospace', 7))


class App(tk.Tk):
    def __init__(self, device, baud):
        super().__init__()
        self.title(TITLE)
        self.configure(bg=BG)
        self.minsize(480, 340)
        self.geometry('900x420')

        hdr = tk.Frame(self, bg=PANEL, pady=6)
        hdr.pack(fill='x')
        tk.Label(hdr, text=TITLE, bg=PANEL, fg=HEADING,
                 font=('monospace', 12, 'bold')).pack(side='left', padx=12)
        self._lbl_status = tk.Label(hdr, text='', bg=PANEL, fg=MUTED,
                                     font=('monospace', 10))
        self._lbl_status.pack(side='right', padx=12)

        self._canvas = BarsCanvas(self)
        self._canvas.pack(fill='both', expand=True, padx=4, pady=4)

        sb = tk.Frame(self, bg=PANEL, pady=3)
        sb.pack(fill='x')
        self._lbl_dev  = tk.Label(sb, text=device, bg=PANEL, fg=MUTED,
                                   font=('monospace', 9))
        self._lbl_dev.pack(side='left', padx=10)
        self._lbl_time = tk.Label(sb, text='', bg=PANEL, fg=CLOCK,
                                   font=('monospace', 9))
        self._lbl_time.pack(side='right', padx=10)
        self._lbl_sats = tk.Label(sb, text='', bg=PANEL, fg=TEXT,
                                   font=('monospace', 9))
        self._lbl_sats.pack(side='right', padx=10)

        self._update()

    def _update(self):
        with _lock:
            sats = {k: dict(v, prn=k) for k, v in _satellites.items()}
            st   = dict(_status)

        if st['connected']:
            self._lbl_status.config(text=f"● {st['device']}", fg=CLOCK)
        else:
            self._lbl_status.config(
                text=f"✗ {st['error'] or st['device']}", fg=ALERT)

        used = st['sats_used']
        seen = st['sats_seen']
        self._lbl_sats.config(
            text=f"Sats: {used} used / {seen} seen",
            fg=CLOCK if used >= 4 else (TEXT if used > 0 else MUTED)
        )
        self._lbl_time.config(text=f"Updated: {st['last_update']}")
        self._canvas.redraw(sats)
        self.after(UPDATE_MS, self._update)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description='GPS C/N0 satellite signal-strength bars',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Cardputer-Adv + Cap LoRa-1262 HAT:\n'
            '  python gps_bars_m8030.py --baud 115200 --device /dev/ttyACM0\n\n'
            'u-blox M8030 / VK-162 dongle:\n'
            '  python gps_bars_m8030.py\n'
        )
    )
    ap.add_argument('--device', default=DEVICE_DEFAULT,
                    help=f'Serial device (default: {DEVICE_DEFAULT})')
    ap.add_argument('--baud',   default=BAUD_DEFAULT, type=int,
                    help=f'Baud rate (default: {BAUD_DEFAULT}; use 115200 for Cardputer-Adv)')
    args = ap.parse_args()

    _status['device'] = args.device

    t = threading.Thread(target=_reader, args=(args.device, args.baud),
                         daemon=True)
    t.start()

    App(args.device, args.baud).mainloop()


if __name__ == '__main__':
    main()
