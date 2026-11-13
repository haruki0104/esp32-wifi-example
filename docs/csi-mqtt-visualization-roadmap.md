# CSI MQTT and live-visualization roadmap

Status: Phase 0 decided and Phase 1 seam implemented; MQTT firmware
publisher, subscribers, and plots are not implemented yet.

Phase 0 baseline (verified 2026-11-07 on ESP32 Dev Module): `pio run`
SUCCESS, host `unittest` 7/7, UDP capture of 50 frames in ~4.3 s with
continuous sequence numbers, 0 drops, RSSI -39, channel 10, 128-byte
payloads, `first_word_invalid` set. Wi-Fi note: the classic ESP32 has no
5 GHz radio; use the 2.4 GHz SSID (`Alice`, not `Alice_5G`).

Phase 0 decisions (locked for Phase 2):
- Broker on the host computer at the stable LAN address (currently
  `192.168.1.113`), port `1883`, trusted LAN only, no auth initially.
  Credentials go in the ignored local `include/secrets.h`
  (`MQTT_BROKER_IP/PORT/USERNAME/PASSWORD` placeholders are in
  `include/secrets.example.h`); never commit real values.
- Default device ID `esp32-csi-01` via `CSI_DEVICE_ID`.
- Topics `csi/v1/<device-id>/frame` (binary CSI1, retain=false, QoS 0)
  and `csi/v1/<device-id>/status` (JSON online/offline, retained, with
  MQTT Last Will for unexpected disconnects).
- First release is QoS 0 live-only. The Arduino MQTT client is NOT yet
  selected: verify it supports the required publish QoS and a packet
  limit of at least ~1024 bytes (536-byte max frame + topic/overhead)
  on-device before adopting it.
- Bounded-backlog policy: on disconnect/overload discard oldest stale
  frames, count transport drops separately from capture-queue drops,
  keep sequence numbers. No unbounded queue or flash archive.

Phase 1 seam (implemented, additive only): `src/csi/CsiFrame.h` value +
wire codec (byte-identical to the UDP path), `src/csi/FramePublisher.h`
narrow interface, `src/csi/UdpFramePublisher` concrete adapter (compiled
but not yet wired into the sketch; the verified `CsiCollector` UDP path
is untouched), host mirror `tools/csi_frame.py` with `FakePublisher` /
`BoundedPublisher` covered by `tools/test_csi_frame.py`. Collector queue
refactor and sketch wiring move to Phase 2 alongside the MQTT adapter.

## Goal

Use one ESP32 as a CSI publisher, a broker on the host computer, and allow
multiple computers to subscribe without opening the ESP32's USB serial data
stream. The host can record the binary frames and render live CSI plots. This
is a transport and visualization feature; it does not add presence or posture
classification.

## Current starting point

- The ESP32 captures AP-BSSID-filtered LLTF CSI and snapshots it into a
  four-slot FreeRTOS queue in `src/csi/CsiCollector.cpp`.
- Each frame is a packed 24-byte little-endian `CSI1` header and up to 512
  payload bytes. The current classic ESP32 LLTF capture has 128-byte payloads.
- The working tree previously contained uncommitted UDP transport changes
  on `feat/csi-mqtt-visualization`, inherited from the preceding CSI-over-UDP
  task. Those changes are now merged from `feat/csi-udp` and hardware-verified
  (50 continuous frames, 0 drops). UDP is the current default transport.
- `tools/csi_udp_read.py` is the current host receiver and stays available
  while introducing MQTT, unless a later decision explicitly removes it.
- The CSI callback must remain limited to validating metadata, copying the
  driver-owned buffer, and queueing it. Do not perform MQTT, socket, logging,
  allocation-heavy, or plotting work in the callback.

## Proposed data flow

```text
ESP32 CSI callback
    -> bounded frame queue
    -> CSI frame/codec
    -> publisher adapter (MQTT initially; UDP remains selectable)
    -> Wi-Fi access point
    -> host MQTT broker
       -> recorder subscriber (JSONL/raw binary)
       -> live-plot subscriber(s)
       -> other analysis subscribers
```

The broker distributes live messages; it is not the archive. A recorder
subscriber must persist frames and detect sequence gaps if data retention is
required.

## Initial protocol and operating decisions

- Preserve the existing `CSI1` frame as the MQTT binary payload. Do not encode
  CSI samples as JSON on the ESP32; JSON is appropriate for low-rate status
  messages, not the raw sample stream.
- Use a unique, stable device identifier. Suggested topics:
  `csi/v1/<device-id>/frame` and `csi/v1/<device-id>/status`.
- Publish frames with `retain=false`; otherwise a new subscriber could mistake
  the last retained sample for a historical capture.
- Start with QoS 0 for live plotting and measure loss. QoS 1 is a later option
  if broker-level delivery matters; it can duplicate messages and does not
  prove that a subscriber wrote data to disk. Subscriber deduplication should
  use device ID plus frame sequence.
- Publish online/offline state on the status topic, using an MQTT Last Will for
  unexpected disconnects.
- Configure broker address, port, device ID, and credentials outside committed
  source. Keep credentials in the ignored local `include/secrets.h` or another
  explicit build-time configuration; never put real values in the example
  file. Keep an unauthenticated broker restricted to a trusted LAN; do not
  expose it to the public internet.
- The current maximum frame is 24 + 512 = 536 bytes. Configure the MQTT
  client's packet/buffer limit to allow that payload plus topic and MQTT
  protocol overhead, and verify the configured limit on-device.
- Start with an explicit bounded-backlog policy. For real-time sensing, prefer
  freshness over delayed replay: when disconnected or overloaded, discard a
  bounded number of stale frames, increment a transport-drop counter, and
  keep sequence numbers so the host can identify gaps. Do not add an
  unbounded queue or flash-backed offline archive in the first iteration.

## ESP32 module and seam

Keep the application layer in `src/sketch_jul29a.ino` responsible for
coordinating Wi-Fi, clock, display, capture, and publisher lifecycle. Do not
put MQTT code in the CSI driver callback.

Refactor in small steps:

1. Define a small `CsiFrame` value containing the existing metadata and
   payload. Keep `FrameHeader`/`tools/csi_read.py::HEADER` wire compatibility;
   add codec tests before changing the transport.
2. Make CSI acquisition enqueue/return frames without knowing whether the
   destination is UDP or MQTT. Keep driver-buffer copying and queue ownership
   inside the CSI module.
3. Put transport behavior behind a narrow publisher interface. The application
   needs only lifecycle/service and frame publication behavior; connection
   retry and client details belong in the adapter. Keep MQTT and the existing
   UDP sender as concrete adapters, and use a fake publisher for host tests.
   Avoid exposing broker-library types to the CSI or application interfaces.
4. Implement MQTT reconnect with a bounded retry/backoff policy. `service()`
   must be called regularly. Do not spin or wait indefinitely when the broker
   is unavailable.
5. Initially service publishing from the main loop, then measure maximum loop
   stall, display refresh jitter, and queue drops. If a synchronous MQTT client
   blocks long enough to affect capture or display, move ownership of the MQTT
   client and transmit queue into a dedicated task; do not call one MQTT client
   concurrently from multiple tasks.
6. Count capture-queue drops and transport publish failures separately, and
   report both in the status topic and OLED/serial diagnostics where space
   permits.

The queue and frame remain internal to the acquisition implementation; do not
add a large public interface just to mirror every implementation method. Test
observable frame bytes, drop behavior, reconnect state, and publisher outcomes
through the publisher seam.

## Host-side modules

Keep the broker, recorder, decoder, and visualizer as separate responsibilities:

- **Broker:** Mosquitto or another standard MQTT broker on the host, restricted
  to the intended LAN and configured with authentication.
- **Recorder:** a host subscriber that subscribes to `csi/v1/+/frame`,
  validates the complete binary `CSI1` payload, annotates host receive time
  and sender/device identity, and appends JSONL or another chosen archive.
- **Decoder:** a reusable host function that validates header/version/chip,
  checks payload length and `first_word_invalid`, and converts samples to
  signed complex pairs. Avoid repeating frame parsing in each visualization.
- **Visualizer:** an MQTT subscriber with a rolling in-memory window. A first
  desktop prototype can use Python, Paho MQTT, NumPy, and Matplotlib animation.
  If a browser dashboard is later required, add a browser MQTT-over-WebSocket
  path or a small host-side relay; keep this out of the ESP32 firmware.

Each desktop that needs a live view can subscribe independently. The recorder
should be a separate subscriber so closing a plot does not stop data capture.

## Live CSI waveform plan

“Waveform” can mean two useful plots; build both incrementally:

1. **Per-frame spectrum line:** horizontal axis is subcarrier index and
   vertical axis is CSI amplitude. For each complex sample, the firmware's
   current payload order is imaginary byte then real byte. Interpret the bytes
   as signed int8 and calculate
   `amplitude = sqrt(real * real + imaginary * imaginary)`.
2. **Scrolling waterfall:** horizontal axis is time/frame sequence, vertical
   axis is subcarrier index, and color is amplitude. This makes changes over
   time easier to see than overlaying many line plots.
3. **Optional activity trace:** show a windowed statistic, such as robust
   amplitude variation against an empty-room baseline. Label it as a signal
   visualization, not a validated presence decision.

If `first_word_invalid` is true, exclude the first four payload bytes (the
first two complex pairs) from features/plots. Validate exact payload length
and CSI configuration, and identify unused carriers before assigning physical
subcarrier labels. Begin with amplitude; phase is more calibration-sensitive
and should not be presented as meaningful without a validated correction.
Measure actual frame rate before choosing the waterfall time axis or smoothing
window. The 100 ms gateway ping interval is not a guarantee of 10 CSI samples
per second.

Suggested first UI: a Python subscriber, a recent-frame amplitude plot, a
rolling waterfall with a fixed time window, and visible device ID, sequence
gaps, frame rate, RSSI, and drop count. Keep the rendering loop bounded and
decouple MQTT reception from plotting with a fixed-size ring buffer so a slow
GUI cannot block the subscriber.

## Implementation phases and acceptance checks

### Phase 0 — Baseline and configuration

- [x] Record current firmware build/test results and actual CSI rate on the
  target board. (Done 2026-11-07: build SUCCESS, 7/7 tests, ~11.6 frames/s
  over UDP with continuous seq.)
- [x] Choose the host broker's stable LAN address, MQTT port, device ID, and
  local credential handling. (Broker `192.168.1.113:1883` trusted-LAN,
  device `esp32-csi-01`, credentials in ignored `secrets.h`.)
- [ ] Decide whether the first release is QoS 0 live-only or requires QoS 1;
  verify the selected Arduino MQTT client actually supports the required
  publish QoS and packet size before adopting it. (Decision: QoS 0
  live-only; client verification still open.)

### Phase 1 — Frame and publisher seam

- [x] Extract the CSI frame value/codec without changing the existing `CSI1`
  wire format. (`src/csi/CsiFrame.h`, byte-identical encoding.)
- [x] Add host tests for encoding/decoding, invalid lengths, chip/version
  checks, and invalid-first-word behavior. (`tools/test_csi_frame.py`.)
- [x] Separate acquisition queueing from UDP/MQTT publication. (Separation
  defined via `FramePublisher` + `UdpFramePublisher`; collector refactor and
  sketch wiring move to Phase 2 to keep the verified UDP path untouched.)
- [x] Add fake-publisher tests for accepted frames, unavailable publisher,
  publish failures, and bounded queue/drop policy. (`FakePublisher` /
  `BoundedPublisher` tests.)
- [x] Keep the existing UDP receiver working as a compatibility check.
  (`tools/csi_udp_read.py` verified on-device 2026-11-07.)

### Phase 2 — MQTT firmware publisher

- [ ] Add and pin the selected PlatformIO MQTT dependency.
- [ ] Configure unique client ID, topic names, broker endpoint, credentials,
  Last Will, QoS, retain policy, and maximum packet size.
- [ ] Implement reconnect/backoff and bounded publish work; ensure callbacks
  never perform MQTT work.
- [ ] Validate that broker loss does not block the firmware indefinitely and
  that reconnect does not produce an unbounded stale backlog.
- [ ] Build `esp32dev`; run Python tests; test with a local broker and at least
  two subscribers; compare sequence gaps and drop counters.

### Phase 3 — Host archive and decoder

- [ ] Add a host MQTT subscriber that archives frames and reports malformed
  payloads and sequence gaps.
- [ ] Reuse the same CSI1 decoder for archive validation and visualization.
- [ ] Test broker disconnect/reconnect, malformed frames, duplicate sequences,
  and separate devices/topics.

### Phase 4 — Dynamic visualization

- [ ] Add a live amplitude spectrum plot and scrolling waterfall.
- [ ] Use a bounded ring buffer and decouple network receive from GUI rendering.
- [ ] Display sample rate, RSSI, device ID, sequence gaps, and firmware/
  transport drops.
- [ ] Verify plotting keeps up with the measured board sample rate and remains
  responsive when frames are malformed or temporarily absent.

### Phase 5 — Field validation

- [ ] Test normal operation, broker restart, Wi-Fi loss, host firewall block,
  and prolonged subscriber absence.
- [ ] Measure ESP32 free heap, loop/task latency, queue high-water mark, and
  dropped frames under each condition.
- [ ] Document tested network/security assumptions and data retention.
- [ ] Keep presence/posture claims out of scope until separate labeled-data
  experiments support them.

## Handoff notes

- Worktree branch: `feat/csi-mqtt-visualization`, now based on the merged
  and hardware-verified `feat/csi-udp` commit. Do not overwrite
  `include/secrets.h`.
- UDP verdict: verified on-device 2026-11-07 (50/50 frames, continuous seq,
  0 drops). `tools/csi_udp_read.py` is the compatibility check.
- Phase 1 seam is in place (C++ codec/interface/UDP adapter + host
  `tools/csi_frame.py` and `tools/test_csi_frame.py`). No MQTT client
  dependency, broker setup, MQTT firmware publisher, MQTT subscriber,
  waveform UI, or physical-device MQTT test has been added yet.
- Recommended next coding task: select and verify the Arduino MQTT client
  (QoS + packet size), then wire the collector through the publisher seam
  and add the MQTT adapter (Phase 2).
