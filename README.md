# ESP32 OLED Clock and CSI Capture

PlatformIO firmware for an ESP32 Dev Module and a 128x64 SSD1306 OLED. It
joins the configured Wi-Fi network, shows Asia/Taipei local time, and streams
Channel State Information from that access point to a host computer over Wi-Fi.

The current target is the original ESP32 (`esp32dev`). An ESP32-S3-DevKitC-1
environment is reserved in `platformio.ini` but is not built: its module
variant and OLED pins are not known, and ESP32-S3 has no GPIO 22.

## What it does

- Connects as a Wi-Fi station. SSID and password come from `include/secrets.h`.
- Starts SNTP and renders local time. The zone string is POSIX `CST-8`.
- After the gateway address is available, enables LLTF CSI, pings the gateway
  every 100 ms, and keeps only packets from the associated AP BSSID.
- Sends each CSI frame as a UDP datagram to the configured host IPv4 address
  and port. USB serial is for boot/status logs and firmware upload, not CSI data.
- Shows the clock on the OLED, with CSI rate and recent drops on the bottom
  line. Before capture starts, that line reads `CSI: waiting`.
- Prints boot and status text, including `CSI_UDP_START` when transmission
  starts. Wi-Fi loss stops capture and retries the connection.

OLED initialization failure hangs in `setup`. A failed NTP wait does not block
CSI once the gateway address is known. This firmware does not classify
presence or posture; see
[docs/csi-human-sensing.md](docs/csi-human-sensing.md) before treating a
capture as a sensing result. The proposed MQTT distribution and live CSI
visualization work is tracked in
[docs/csi-mqtt-visualization-roadmap.md](docs/csi-mqtt-visualization-roadmap.md);
it is a plan, not an implemented feature.

## Hardware and display

The ESP32 environment wires the SSD1306 at I2C `0x3C`, SDA 21, SCL 22, with no
reset pin. The 128x64 screen shows:

- `HH:MM:SS`, or `--:--:--` before SNTP sync
- `YYYY-MM-DD` and weekday, or `Waiting for NTP time`
- `CSI <rate>/s d<drops>` once capture is running

## Layers

- `src/sketch_jul29a.ino` — startup, reconnect, and the only cross-layer calls.
- `src/network/` — station connection and connection state.
- `src/time/` — SNTP startup, sync check, and local-time access.
- `src/csi/` — CSI configuration, gateway ping, callback snapshot, and UDP
  frame transmission.
- `src/display/` — OLED initialization and rendering only.

Display code does not join Wi-Fi or start CSI. CSI and network code do not
draw. Espressif's separate ESP-IDF examples are reference only; flashing one
replaces this application. See
[docs/esp-csi-firmware.md](docs/esp-csi-firmware.md).

## CSI frame

Each UDP datagram contains one 24-byte little-endian header followed by the
unmodified CSI buffer. The frame format remains compatible with the serial
reader and existing captures:

| Field | Meaning |
|---|---|
| magic | `CSI1` |
| version | `1` |
| chip | `1` on this ESP32; `2` is reserved for ESP32-S3 |
| len | payload bytes, at most 512 |
| seq | sequence of queued samples |
| rssi | dBm |
| channel | primary channel |
| first_word_invalid | non-zero means the first four payload bytes are invalid |
| mac | source MAC, filtered to the AP BSSID |
| drops | samples dropped since the previous emitted frame |

On the ESP32 Dev Module used here, LLTF payloads are 128 bytes and
`first_word_invalid` is set. The host stores those bytes as hex and does not
guess their I/Q layout.

## Branch workflow

- `main` contains release-ready code.
- `dev` is the integration branch.
- Create `feat/<short-description>` for new work or
  `fix/<short-description>` for bug fixes, branching from `dev`.
- Open pull requests from `feat/*` or `fix/*` into `dev`. Once verified, open
  a pull request from `dev` into `main`.
- PlatformIO CI builds push and pull-request changes targeting `main` or `dev`.
  It compiles with `include/secrets.example.h`; no real WiFi credentials are
  needed or exposed in CI.

## Build, upload, and capture

```sh
pio run -e esp32dev
pio run -e esp32dev -t upload --upload-port /dev/ttyUSBx
```

Replace `/dev/ttyUSBx` with the board's serial device. USB serial is used for
firmware upload and status logs; it is not used to capture CSI.

Before building, copy `include/secrets.example.h` to `include/secrets.h` and
fill in the Wi-Fi credentials and set `CSI_HOST_IP` to the host computer's
local IPv4 address. `CSI_UDP_PORT` defaults to `5005`. Use a stable host address
or a DHCP reservation so the ESP32 continues to find the receiver. If omitted
from an existing `secrets.h`, the firmware defaults to `192.168.1.100:5005`;
override the IP for your LAN. The sketch includes that header. The real file is
gitignored, and a missing file fails compilation instead of using the example
Wi-Fi credentials. Connection and NTP timeouts are constants in the sketch.

Start the host receiver before the ESP32 sends data. Allow inbound UDP on the
selected port in the host firewall, and keep both devices on a network that
allows client-to-client traffic:

```sh
python3 tools/csi_udp_read.py --listen 0.0.0.0 --port 5005 --count 1000
```

Start this receiver before booting or resetting the ESP32.
`--listen` defaults to `0.0.0.0`; omit `--count` to capture until Ctrl-C or use
`--timeout 60` for a 60-second run. `tools/csi_udp_read.py` writes one JSON
object per valid datagram to
`captures/csi-<UTC timestamp>.jsonl` unless `-o` selects another path. With
no `--count` or `--timeout`, it runs until Ctrl-C. Fields include host UTC
receive time, ESP32 sender IP/port, sequence, RSSI, channel, source MAC, drop
count, and raw payload hex. That timestamp is when the computer handled the
datagram, not the radio reception time. UDP does not retransmit lost datagrams;
use sequence numbers and drop counts to identify gaps. This first version is
intended for a trusted local network, not an untrusted or internet-facing
network. The OLED CSI rate counts frames queued locally, not confirmed host
receipts; UDP has no acknowledgement.

The older `tools/csi_read.py` utility remains available for serial captures
from compatible firmware; install `tools/requirements-csi.txt` to use it. The
current firmware sends CSI over UDP. Capture files are gitignored.

Parser tests:

```sh
python3 -m unittest discover -s tools -p 'test_*.py'
```
