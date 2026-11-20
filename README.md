# ESP32 OLED Clock and CSI Capture

PlatformIO firmware for an ESP32 Dev Module and a 128x64 SSD1306 OLED. It
joins the configured Wi-Fi network, shows Asia/Taipei local time, and streams
Channel State Information from that access point to a host computer over Wi-Fi.

M0 topology: the classic ESP32 is the TX node (`esp32_tx`, node 255,
with OLED); the two ESP32-S3-N16R8 boards are RX nodes (`esp32s3_rx1`
node 1, `esp32s3_rx2` node 2, headless via `-D NO_OLED`). A spare
`esp32_rx1` env remains for a classic-ESP32 RX if needed.
ESP32-C5 cannot be targeted: the pinned platform and Arduino core ship no
C5 board definitions, so M0 runs on ESP32 + S3.

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
Phase 0 is decided and the Phase 1 frame/publisher seam is implemented
(`src/csi/CsiFrame.h`, `src/csi/FramePublisher.h`, host `tools/csi_frame.py`),
while MQTT publishing is not implemented yet. UDP remains the default
transport. Host-side plotting and analysis tools exist; see
"Host analysis tools" below.

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

Each UDP datagram contains one 32-byte little-endian v2 header followed by
the unmodified CSI buffer. Version 1 (24-byte) captures stay readable on
the host; the firmware emits version 2 only:

| Field | Meaning |
|---|---|
| magic | `CSI1` |
| version | `2` |
| chip | `1` ESP32, `2` ESP32-S3 |
| len | payload bytes, at most 512 |
| seq | per-node sequence, wraps |
| rx_ts_us | `esp_timer_get_time()` on the RX node at capture |
| node_id | receiver identity (`-D CSI_NODE_ID`; 255 = TX, 0 = unset) |
| flags | bit0 first-word-invalid, bits1-2 bandwidth, bit3 TX/RX role |
| rssi | dBm |
| channel | primary channel |
| drops | samples dropped since the previous emitted frame |
| mac | transmitter (peer) MAC; RX nodes filter to the TX node |

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
pio run
pio run -e esp32_tx -t upload --upload-port /dev/ttyUSBx
pio run -e esp32s3_rx1 -t upload --upload-port /dev/ttyUSBy
pio run -e esp32s3_rx2 -t upload --upload-port /dev/ttyUSBz
```

Replace `/dev/ttyUSBx` with each board's serial device (S3 boards enumerate
as USB CDC; if no port appears, hold BOOT while pressing RESET). Flash the
TX build onto the classic ESP32 and one RX build per S3 board (node ids
1 and 2 are baked in via `-D CSI_NODE_ID`).

Join the host computer to the `csi-tx` softAP network (or route to it) and
set `CSI_HOST_IP` to the host's address on that network so RX frames and
TX heartbeats arrive. The TX board broadcasts the measurement stream while
RX boards associate to it as stations. USB serial is used for
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

## Host analysis tools

Beyond archiving raw frames, the host can log time-series amplitude,
plot captures, and run an adaptive activity detector prototype. All host
analysis needs `numpy` and `matplotlib`:

```sh
python3 -m pip install -r tools/requirements-plot.txt
```

### Time-vs-amplitude log

`tools/csi_time_log.py` subscribes to the same UDP stream and appends one
CSV row per frame: host receive time, sequence and gap count, RSSI,
channel, per-frame amplitude mean/std/min/max, and firmware drops.
`--per-subcarrier` adds one column per subcarrier; `--replay` converts an
existing JSONL capture without needing the board:

```sh
python3 tools/csi_time_log.py --port 5005 -o captures/amp.csv --per-subcarrier
python3 tools/csi_time_log.py --replay captures/csi-xxx.jsonl -o /tmp/amp.csv
```

### Capture plots

`tools/csi_plot_csv.py` renders headless PNGs from those CSVs without
opening a window: single-file charts, two-file overlays (`--compare`), and
time-vs-subcarrier heatmaps (`--heatmap`, needs `--per-subcarrier`
columns) with posture segments shaded via
`--mark "still:0-10,wave:10-30,still:30-40"`.

### Live plot and live detector

`tools/csi_live_plot.py` draws the per-frame amplitude spectrum and a
scrolling waterfall in real time (`--vmax` fixes the color scale so temporal
changes are not renormalized away; `--diff` paints deviation from the
window baseline). With `--detect` it adds a third panel: the live detection
z-score with an ACTIVE/quiet banner, plus a `DET:ACTIVE` flag in the status
line.

`--output/-o PATH` archives every received frame as the same JSONL records
as `tools/csi_udp_read.py` while plotting. Without it the plotter keeps only
the rolling window in memory (`--window` frames, a few seconds) and writes
nothing, so the run cannot be re-scored offline afterwards; use
`-o captures/live-<UTC>.jsonl` for any session whose data matters. `--replay`
reads a capture and never writes one.

The live detector runs the same causal algorithm as the offline prototype
(`tools/csi_detect.py`): each frame's energy (mean absolute deviation from
the trailing per-carrier median) is median-smoothed over 3 s and compared
against a slow trailing baseline (median/MAD) as a z-score with hysteresis
(enter 3.0, exit 1.5, minimum duration 2 s, 5 s warmup). Only past frames
are ever used, so the background drift moves the baseline instead of
tripping the detector. The live baseline is the rolling GUI window, so use
`--window 300+` for a slower baseline. The z panel shows a rolling time
window (`--zspan`, default last 60 s) instead of time-since-start, so the
origin scrolls with the data.

All detection thresholds live in `tools/csi_detect.json` (versioned, hand
tunable: baseline/smoothing windows, hysteresis levels, minimum duration,
warmup). Both the offline tool (`--config PATH`) and the live plotter
(`--detect-config PATH`) read it, and explicit CLI flags override file
values. It is a display aid: the offline
tool on a full CSV remains the reference for scoring, and thresholds are
provisional until walk-through experiments set the operating range. As with
all CSI work here, this visualizes signal variation; it does not classify
presence or posture (see `docs/csi-human-sensing.md`).
