# Espressif ESP-CSI firmware

Research checked on 2026-11-03 against Espressif's `esp-csi` repository at
commit [`8633d671`](https://github.com/espressif/esp-csi/tree/8633d67152db2808f141cc1595970aa9cf406045).

## Recommended example for this board

Start with Espressif's
[`examples/get-started/csi_recv_router`](https://github.com/espressif/esp-csi/tree/8633d67152db2808f141cc1595970aa9cf406045/examples/get-started/csi_recv_router).
It is the closest match for this project's ESP32 Dev Module and existing WiFi
router: the ESP32 joins a configured network, pings its gateway, and collects
CSI from the router's replies. The example prints `CSI_DATA` records over
UART. Its README documents selecting the target, configuring the connection
with `idf.py menuconfig`, and flashing/monitoring with `idf.py -p PORT flash
monitor`.

The repo also has
[`csi_recv`](https://github.com/espressif/esp-csi/tree/8633d67152db2808f141cc1595970aa9cf406045/examples/get-started/csi_recv)
and [`csi_send`](https://github.com/espressif/esp-csi/tree/8633d67152db2808f141cc1595970aa9cf406045/examples/get-started/csi_send)
for an ESP-NOW receiver/transmitter pair. That option needs another ESP32
running the sender; it is less direct for a one-board setup.

These are source examples, not drop-in firmware binaries. The router example
is an ESP-IDF CMake application (`app_main`), while this project is a
PlatformIO/Arduino sketch (`setup`/`loop`). Flashing the example replaces the
current application; it will not retain this project's clock and OLED
features. To combine them, port the CSI initialization and data collection
into this application, then keep the existing WiFi/time/display modules and
add a dedicated CSI collection module.

## Main differences

| Normal firmware in this project | CSI-enabled example |
|---|---|
| PlatformIO with the Arduino framework; `setup()`/`loop()` coordinate WiFi, SNTP time, and OLED rendering. | ESP-IDF application using `app_main()`, ESP-IDF WiFi APIs, and CMake. |
| Connects to the configured WiFi network and displays local time/date. | Configures CSI capture, registers a WiFi CSI receive callback, and enables CSI collection. The router example generates ping traffic to the gateway to elicit packets. |
| No CSI configuration, callback, or CSI data output. | Enables the CSI driver option in `sdkconfig.defaults` and emits metadata plus CSI sample values as `CSI_DATA` over UART. The example's default console baud is 921600, so the monitor baud differs from this project's 115200. |

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
