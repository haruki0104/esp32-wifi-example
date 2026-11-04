# ESP32 OLED Clock

PlatformIO firmware for an ESP32 Dev Module with a 128x64 SSD1306 OLED. It
connects to WiFi, synchronizes the system clock with SNTP, and displays local
time in Asia/Taipei.

## Layers

- `src/sketch_jul29a.ino` — application startup and coordination.
- `src/network/` — WiFi station connection and connection state.
- `src/time/` — SNTP startup, synchronization checks, and local-time access.
- `src/display/` — OLED initialization and rendering.

The application coordinates the modules; display code does not perform
network or time synchronization, and network/time code does not render UI.
This keeps later features, such as a sensor or CSI data display, localized to
the relevant layer and application flow.

For Espressif's CSI firmware examples and how they differ from this Arduino
application, see [docs/esp-csi-firmware.md](docs/esp-csi-firmware.md).

## Build and upload

```sh
pio run
pio run -t upload --upload-port /dev/ttyUSBx
pio device monitor -p /dev/ttyUSBx -b 115200
```

Replace `/dev/ttyUSBx` with the serial device for your board. Before building,
copy `include/secrets.example.h` to `include/secrets.h` and fill in the WiFi
credentials. The sketch includes this header, so the C++ compiler reads the
local configuration while compiling; the credentials are not embedded as
source-code literals in the application file. The real secrets file is ignored
by Git. If it is missing, compilation fails rather than silently using example
credentials. Connection and synchronization timeouts are configured in the
application sketch.
