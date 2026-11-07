# AGENTS.md

PlatformIO Arduino firmware (`espressif32@7.0.1`) for an ESP32 Dev Module, plus a host script that records its CSI stream. Behavior and commands are in `README.md`. Branch from `dev` as `feat/<short>` or `fix/<short>`; pull requests land in `dev`, then `dev` into `main`.

## Verify

From the repo root:

- `python3 -m unittest discover -s tools -p 'test_*.py'` — the only automated tests. There is no `pio test` target.
- `pio run -e esp32dev` — the only firmware env. `default_envs` is `esp32dev`. Leave the commented `esp32s3` env commented: its module variant and OLED pins are unknown, and ESP32-S3 has no GPIO 22.

`include/secrets.h` is required to compile and is gitignored. Create it from `include/secrets.example.h` only when the file is missing. Do not overwrite an existing file, and do not commit it.

## Contracts

- UART is 115200 for boot/status logs; CSI frames are sent over UDP, not serial. `CSI_HOST_IP` and `CSI_UDP_PORT` in `include/secrets.h` select the host receiver. Each UDP datagram contains exactly one `CSI1` frame. The CSI RX callback only copies into the FreeRTOS queue; `CsiCollector::sendPending` is the only place that transmits frame bytes. UDP can lose datagrams; use sequence and drop counts to detect gaps. Upload speed in `platformio.ini` is 921600 and is not the log or UDP capture rate.
- Keep `FrameHeader` in `src/csi/CsiCollector.cpp` and `HEADER` in `tools/csi_read.py` in lockstep (packed 24-byte little-endian header, magic `CSI1`, chip `1` on this ESP32). `tools/csi_udp_read.py` validates that same frame. The host tools store payload hex and do not decode I/Q.
- Leave `WiFi.setSleep(false)` in place while CSI is running. Leave the POSIX zone string `CST-8` (Asia/Taipei). `CST+8` is the opposite offset; `configTzTime` takes a POSIX string, not an IANA name.
- `src/sketch_jul29a.ino` is the only file that calls across `src/network/`, `src/time/`, `src/csi/`, and `src/display/`.
- OLED pins come from `-D OLED_SDA` / `-D OLED_SCL` in `platformio.ini`. Missing flags fail the build.
- This application stays on the board. Espressif `esp-csi` examples are reference only (`docs/esp-csi-firmware.md`). This firmware does not classify presence or posture (`docs/csi-human-sensing.md`).

Ignored, and not for commits: `.pio/`, `.vscode/` (generated; change `platformio.ini` instead), `captures/`, `include/secrets.h`.
