# Espressif ESP-CSI firmware

This application collects CSI in `src/csi/` and keeps the clock and OLED.
Do not flash the example below onto this board. The example is the reference
for router-ping capture, not a replacement firmware.

Research checked on 2026-11-03 against Espressif's `esp-csi` repository at
commit [`8633d671`](https://github.com/espressif/esp-csi/tree/8633d67152db2808f141cc1595970aa9cf406045).

## Reference example

Espressif's
[`examples/get-started/csi_recv_router`](https://github.com/espressif/esp-csi/tree/8633d67152db2808f141cc1595970aa9cf406045/examples/get-started/csi_recv_router)
is the closest published match for this ESP32 Dev Module: it joins a
configured network, pings the gateway, and collects CSI from the router. It
prints `CSI_DATA` text over UART. Read it for the method. Do not flash it
over this project.

The repo also has
[`csi_recv`](https://github.com/espressif/esp-csi/tree/8633d67152db2808f141cc1595970aa9cf406045/examples/get-started/csi_recv)
and [`csi_send`](https://github.com/espressif/esp-csi/tree/8633d67152db2808f141cc1595970aa9cf406045/examples/get-started/csi_send)
for an ESP-NOW receiver/transmitter pair. That option needs another ESP32
running the sender; it is less direct for a one-board setup.

These are source examples, not drop-in firmware binaries. The router example
is an ESP-IDF CMake application (`app_main`). This project is a
PlatformIO/Arduino sketch (`setup`/`loop`) that already contains its own CSI
collector. Flashing the example replaces the clock, OLED, and `CSI1` stream.

## Main differences

| This firmware | `csi_recv_router` example |
|---|---|
| PlatformIO Arduino, `espressif32@7.0.1`. `setup()`/`loop()` coordinate Wi-Fi, SNTP, OLED, and CSI. | ESP-IDF `app_main()` and CMake. Flashing it removes this application. |
| LLTF only, AP-BSSID filter, 100 ms gateway ping, binary `CSI1` frames at 115200 after `CSI_STREAM_START`. | Decimal `CSI_DATA` text. Its default console baud is 921600. |
| CSI is already enabled in the prebuilt Arduino Wi-Fi library. AMPDU and RX-buffer settings from the example `sdkconfig.defaults` are not applied. | Enables CSI in `sdkconfig.defaults` and can change Wi-Fi buffer and AMPDU options. |

The router example's component manifest declares ESP-IDF `>=4.4.1` and the
`esp_csi_gain_ctrl` component. Its `sdkconfig.defaults` enables CSI and adjusts
WiFi receive-buffer settings. Refer to the exact example files below before
building; CSI configuration fields can differ among ESP32 chip generations.

## Build and flash outline

Run these commands from an ESP-IDF-enabled terminal after installing ESP-IDF
and its toolchain:

```sh
git clone https://github.com/espressif/esp-csi.git
cd esp-csi/examples/get-started/csi_recv_router
idf.py set-target esp32
idf.py menuconfig
idf.py -p /dev/ttyUSBx flash monitor
```

In `menuconfig`, configure the WiFi connection for the router. Replace
`/dev/ttyUSBx` with the ESP32's actual serial device. `idf.py` commands are
not interchangeable with `pio run`: this is a separate ESP-IDF build.

## Sources

- [ESP-CSI overview and example catalog](https://github.com/espressif/esp-csi/blob/8633d67152db2808f141cc1595970aa9cf406045/README.md)
- [Router-receiver example source (`app_main.c`)](https://github.com/espressif/esp-csi/blob/8633d67152db2808f141cc1595970aa9cf406045/examples/get-started/csi_recv_router/main/app_main.c)
- [Router-receiver build and flash instructions](https://github.com/espressif/esp-csi/blob/8633d67152db2808f141cc1595970aa9cf406045/examples/get-started/csi_recv_router/README.md)
- [Router-receiver CSI configuration](https://github.com/espressif/esp-csi/blob/8633d67152db2808f141cc1595970aa9cf406045/examples/get-started/csi_recv_router/sdkconfig.defaults)
- [Router-receiver ESP-IDF component manifest](https://github.com/espressif/esp-csi/blob/8633d67152db2808f141cc1595970aa9cf406045/examples/get-started/csi_recv_router/main/idf_component.yml)
- [CSI receiver example source (`app_main.c`)](https://github.com/espressif/esp-csi/blob/8633d67152db2808f141cc1595970aa9cf406045/examples/get-started/csi_recv/main/app_main.c)
- [ESP-IDF WiFi API reference](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/network/esp_wifi.html)
