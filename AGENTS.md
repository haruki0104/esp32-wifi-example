# AGENTS.md

PlatformIO Arduino firmware (`espressif32@7.0.1`) for an ESP32 Dev Module, plus a host script that records its CSI stream. Behavior and commands are in `README.md`. Branch from `dev` as `feat/<short>` or `fix/<short>`; pull requests land in `dev`, then `dev` into `main`.

## Verify

From the repo root:

- `python3 -m unittest discover -s tools -p 'test_*.py'` — the only automated tests. There is no `pio test` target.
- `pio run` builds `default_envs` (`esp32_tx`, `esp32s3_rx1`, `esp32s3_rx2`). Roles come from `-D CSI_NODE_ID` (1..254 RX, 255 TX softAP, 0 unset). Inventory: 1x classic ESP32 (TX) + 2x ESP32-S3-N16R8 (RX, headless). ESP32-C5 has no env: the pinned platform/Arduino core ships no C5 board definitions.

`include/secrets.h` is required to compile and is gitignored. Create it from `include/secrets.example.h` only when the file is missing. Do not overwrite an existing file, and do not commit it.

## Contracts

- Serial logs are 115200 baud (UART on classic ESP32, USB CDC on S3 builds with `ARDUINO_USB_CDC_ON_BOOT=1`); CSI frames are sent over UDP, not serial. `CSI_HOST_IP` and `CSI_UDP_PORT` in `include/secrets.h` select the host receiver. Each UDP datagram contains exactly one `CSI1` frame. The CSI RX callback only copies into the FreeRTOS queue; `CsiCollector::sendPending` is the only place that transmits frame bytes. UDP can lose datagrams; use sequence and drop counts to detect gaps. Upload speed in `platformio.ini` is 921600 and is not the log or UDP capture rate.
- Keep `CsiFrameV2.h` (`src/csi/`) and `HEADER_V2` in `tools/csi_read.py` in lockstep (packed 32-byte little-endian header, magic `CSI1`, version `2`; firmware emits v2 only, while version 1 stays readable on the host for old captures). `tools/csi_udp_read.py` validates that same frame. The host tools store payload hex and do not decode I/Q.
- RX nodes filter to the TX softAP BSSID and forward to `CSI_HOST_IP`; the TX node (`CSI_NODE_ID=255`) runs the softAP, broadcasts the measurement stream, and unicasts empty heartbeat frames (role TX). Only `src/sketch_jul29a.ino` starts these modes.
- Leave `WiFi.setSleep(false)` in place while CSI is running. Leave the POSIX zone string `CST-8` (Asia/Taipei). `CST+8` is the opposite offset; `configTzTime` takes a POSIX string, not an IANA name.
- `src/sketch_jul29a.ino` is the only file that calls across `src/network/`, `src/time/`, `src/csi/`, and `src/display/`.
- OLED pins come from `-D OLED_SDA` / `-D OLED_SCL` in `platformio.ini`. Missing flags fail the build, except on `-D NO_OLED` boards (S3-N16R8 without an external OLED), where the display is a no-op and `begin()` succeeds so `setup()` never blocks.
- This application stays on the board. Espressif `esp-csi` examples are reference only (`docs/esp-csi-firmware.md`). This firmware does not classify presence or posture (`docs/csi-human-sensing.md`).

Ignored, and not for commits: `.pio/`, `.vscode/` (generated; change `platformio.ini` instead), `captures/`, `include/secrets.h`.

## Parallel agents

- One agent per git worktree; never two agents in the same working tree.
- Claim files before editing: comment on the relevant PR (or open one).
  If another agent is mid-edit on a file, hold until its change lands.
- One author implements, the other reviews; do not open a parallel branch
  editing the same files without agreeing first on the PR thread.
- Do not rewrite another agent's pushed branch. Land PRs into `dev` in
  dependency order.
