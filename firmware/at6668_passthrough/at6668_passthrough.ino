/*
 * at6668_passthrough.ino
 *
 * USB-CDC ↔ GNSS UART bridge for the M5Stack Cardputer-Adv + Cap LoRa-1262 HAT.
 *
 * HARDWARE
 * --------
 *   Board  : M5Stack Cardputer-Adv (ESP32-S3, 8 MB flash, HWCDC)
 *   HAT    : M5Stack Cap LoRa-1262 (SKU U214)
 *             - LoRa : SX1262
 *             - GNSS : ATGM336H (CASIC multi-constellation, labelled AT6668
 *                      in some M5Stack documentation)
 *
 * WIRING (from Cap LoRa-1262 schematic)
 * ------
 *   GPS TX  → ESP32-S3 GPIO 15  (Serial1 RX)
 *   GPS RX  ← ESP32-S3 GPIO 13  (Serial1 TX)
 *   Baud    : 115200 8N1
 *
 * GNSS CHIP NOTE
 * --------------
 *   The ATGM336H uses the CASIC command set ($PCAS…), NOT the MediaTek
 *   $PMTK or Airoha $PAIR commands.  The factory default only outputs
 *   $GNGGA and $GNRMC.  The setup() below sends:
 *
 *     $PCAS03,1,0,1,1,1,0,0,0,0,0,0,0,0,0   enable GGA+GSA+GSV+RMC
 *     $PCAS00                                 save config to GPS flash
 *
 *   After PCAS00 the settings survive GPS power-off, so future boots do
 *   not strictly need re-initialisation — but we send them anyway as a
 *   safety net.
 *
 * COMPILE FLAGS (arduino-cli)
 * ---------------------------
 *   --fqbn "esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc"
 *
 * USAGE
 * -----
 *   Flash once, then open /dev/ttyACM* at any baud (USB-CDC ignores baud).
 *   NMEA sentences including GPGSV/GLGSV/GAGSV/BDGSV flow immediately.
 *   Run tools/gps_bars_m8030.py --baud 115200 --device /dev/ttyACMx for
 *   a live satellite signal-strength bar display.
 */

// ── PCAS helper ──────────────────────────────────────────────────────────────

// Appends an NMEA checksum to `body` and writes the full sentence to Serial1.
static void pcas(const char *body) {
  uint8_t cs = 0;
  for (const char *p = body; *p; p++) cs ^= (uint8_t)*p;
  char buf[96];
  snprintf(buf, sizeof(buf), "$%s*%02X\r\n", body, cs);
  Serial1.print(buf);
}

// ── Arduino entry points ──────────────────────────────────────────────────────

void setup() {
  // USB-CDC to host — baud value is symbolic for HWCDC; actual rate is USB
  Serial.begin(115200);

  // ATGM336H UART — GPIO 15 = RX (from GPS TX), GPIO 13 = TX (to GPS RX)
  Serial1.begin(115200, SERIAL_8N1, 15, 13);

  // Allow the GPS chip time to finish its own startup before sending commands
  delay(500);

  // Enable GGA(1) GLL(0) GSA(1) GSV(1) RMC(1) VTG(0) and reserved fields(0)
  pcas("PCAS03,1,0,1,1,1,0,0,0,0,0,0,0,0,0");

  // Persist the above settings to the GPS chip's internal flash
  pcas("PCAS00");

  Serial.println("# AT6668 passthrough ready");
}

void loop() {
  // Forward every byte from the GPS chip to USB-CDC
  while (Serial1.available()) Serial.write(Serial1.read());

  // Forward every byte from USB-CDC to the GPS chip (for live configuration)
  while (Serial.available())  Serial1.write(Serial.read());
}
