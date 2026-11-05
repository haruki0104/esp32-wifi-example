# CSI collection and human-sensing analysis

Research checked on 2026-11-04. This guide describes a local, opt-in
experimental setup; CSI measurements can reveal activity in a space, so collect
them only where occupants know about and consent to the sensing.

## What this project captures

The current ESP32 firmware associates with the configured access point, pings
its gateway every 100 ms, filters received CSI to the access point BSSID, and
streams binary `CSI1` frames over the board UART at 115200. The computer-side
[`csi_read.py`](../tools/csi_read.py) saves each frame as one JSONL record in
`captures/`. It preserves the raw payload rather than silently interpreting it.

The capture currently contains host UTC receive time, sequence number, RSSI,
Wi-Fi channel, source MAC, `first_word_invalid`, firmware drop count, payload
length, and raw payload bytes encoded as hex. The timestamp is when the host
process received the serial frame; this firmware frame does not include the
radio's CSI reception timestamp.

The firmware enables LLTF CSI only. In this ESP32 configuration a 128-byte
payload is 64 pairs of signed bytes. Espressif documents each pair as
imaginary-part byte followed by real-part byte. If `first_word_invalid` is set,
the first four CSI bytes (two pairs) are invalid and should be excluded from
features. These details are chip-, packet-, bandwidth-, and configuration-
dependent; do not assume every ESP target or CSI mode has the same layout.
See Espressif's [ESP-IDF CSI guide](https://github.com/espressif/esp-idf/blob/0e6b480f858c69862ffb6001d96e9eef74599a6e/docs/en/api-guides/wifi-driver/wifi-vendor-features.rst)
and the project's [`CsiCollector`](../src/csi/CsiCollector.cpp) configuration.

## Practical analysis path

### 1. Validate and characterize captures

- Capture separate sessions with a fixed board, AP, channel, antenna
  orientation, and room layout. Record these conditions alongside each session.
- Check sequence gaps and `firmware_drops_since_previous_frame`; discard or
  mark windows with excessive loss.
- Check `first_word_invalid`, payload length, channel, and RSSI before feature
  extraction. Do not mix different CSI modes or payload lengths in one model.
- The current ping-driven receiver produces samples only when matching AP
  packets arrive. Measure its actual sample rate before choosing analysis
  windows; the configured ping interval is not a guarantee of CSI rate.

### 2. Derive interpretable features

For the current classic ESP32 LLTF stream, read each byte as signed int8 and
reshape valid payload bytes into `(imaginary, real)` pairs. For each
subcarrier:

```text
amplitude = sqrt(real² + imaginary²)
phase     = atan2(imaginary, real)
```

Start with amplitude features because raw phase is sensitive to receiver and
environment offsets. For each short time window, calculate robust per-
subcarrier median, interquartile range or standard deviation, and changes from
an empty-room baseline. Exclude invalid/unused subcarriers and inspect data
before applying smoothing. Phase-based features should only be added after a
calibration procedure is established and validated for this exact device and
setup.

### 3. Establish presence detection first

Presence is the realistic first classification target:

1. Record multiple empty-room baseline sessions.
2. Record opt-in occupied sessions with different everyday movements, plus
   empty-room disturbances such as HVAC, doors, and Wi-Fi traffic.
3. Use a simple threshold on windowed CSI variation as a baseline, then compare
   it to a small classifier. Add temporal hysteresis so one noisy frame does
   not flip the state.
4. Report precision, recall, false alarms per hour, and missed-presence rate.
   Split evaluation by capture session/day instead of randomly splitting
   adjacent windows, which can make results look unrealistically good.

Espressif's [`wifi_sensing_demo`](https://github.com/espressif/esp-csi/tree/8633d67152db2808f141cc1595970aa9cf406045/examples/esp-radar/wifi_sensing_demo)
is a useful first-party reference for an ACTIVE/INACTIVE sensing state machine
and tunable thresholds. It is a separate ESP-IDF demo/component, not a model
that can be assumed to run on this project's Arduino capture stream unchanged.

### 4. Treat posture as a separate, harder experiment

Standing/sitting/lying classification requires time-windowed labeled examples
for every class, a fixed protocol, and evaluation across sessions and room
conditions. Begin with only a few coarse classes and report a per-class
confusion matrix. A single CSI link provides a narrow measurement of one
propagation path; it does not directly observe a full body pose. Performance
may change substantially with board placement, antenna orientation, furniture,
room geometry, AP traffic, and people moving outside the target area.

The primary *Person-in-WiFi* study demonstrated body segmentation and pose
estimation using a materially richer setup: three transmit antennas and three
receive antennas, image-derived labels, over 100,000 frames, and 16 indoor
scenes. It is evidence that pose inference can be researched from Wi-Fi
signals, not evidence that the present one-ESP32/router link can reproduce
that accuracy. Source: [Wang et al., Person-in-WiFi (arXiv:1904.00276)](https://arxiv.org/abs/1904.00276).

Do not claim posture detection until a labeled test set from this exact hardware
and deployment demonstrates useful, repeatable performance. If the goal is
only room occupancy, do not collect or retain the more detailed posture labels
or raw data longer than necessary.

## Example collection

```sh
python3 -m pip install -r tools/requirements-csi.txt
python3 tools/csi_read.py -p /dev/ttyUSB1 --count 1000
python3 tools/csi_read.py -p /dev/ttyUSB1 --timeout 300 -o captures/empty-room-01.jsonl
```

Close any other serial monitor before capture. The default capture continues
until Ctrl-C; `--count` and `--timeout` can bound a run. Capture files are
ignored by Git. Keep session notes separately and avoid recording personal
identifiers.

## Primary sources

- [Espressif ESP-IDF Wi-Fi CSI guide](https://github.com/espressif/esp-idf/blob/0e6b480f858c69862ffb6001d96e9eef74599a6e/docs/en/api-guides/wifi-driver/wifi-vendor-features.rst): CSI composition, per-chip payload layout, invalid first word, API setup, and callback constraints.
- [Espressif ESP-IDF Wi-Fi API header](https://github.com/espressif/esp-idf/blob/0e6b480f858c69862ffb6001d96e9eef74599a6e/components/esp_wifi/include/esp_wifi.h): CSI callback registration, configuration, and enable APIs.
- [Espressif `esp-csi` router receiver source](https://github.com/espressif/esp-csi/blob/8633d67152db2808f141cc1595970aa9cf406045/examples/get-started/csi_recv_router/main/app_main.c): gateway ping traffic, BSSID filtering, CSI callback, metadata, and sample output.
- [Espressif `esp-csi` get-started documentation](https://github.com/espressif/esp-csi/blob/8633d67152db2808f141cc1595970aa9cf406045/examples/get-started/README.md): receiver/transmitter setup and sample CSI format.
- [Espressif Wi-Fi sensing demo](https://github.com/espressif/esp-csi/tree/8633d67152db2808f141cc1595970aa9cf406045/examples/esp-radar/wifi_sensing_demo): first-party ACTIVE/INACTIVE sensing example and threshold controls.
- [Wang et al., *Person-in-WiFi: Fine-grained Person Perception using WiFi*](https://arxiv.org/abs/1904.00276): original pose/body-segmentation study and its multi-antenna, labeled-data setup.
