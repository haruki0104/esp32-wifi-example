# M0 acceptance run

What this proves: one TX node (classic ESP32, softAP + broadcast) and two RX
nodes (ESP32-S3-N16R8) each produce v2 frames that the host can merge onto one
timeline, with per-node sequence accounting and a stable TX link.

What it does not prove: anything about presence or posture. This firmware does
not classify; see `csi-human-sensing.md`.

Architecture and the frozen frame layout: `csi-multi-node-design.md`.

## Before flashing

`include/secrets.h` (gitignored; copy from `include/secrets.example.h`):

| key | value for this run |
| --- | --- |
| `CSI_TX_SSID`, `CSI_TX_PASSWORD` | the measurement network; RX nodes join it, the host must join it too |
| `CSI_HOST_IP` | the **host's** address **on the `CSI_TX_SSID` network** |
| `CSI_UDP_PORT` | `5005` — RX CSI frames and TX heartbeats both land here |

The TX softAP has no uplink, so the host has to be a client of that AP. A host
on the home network cannot receive the broadcast at all.

Ports, so nobody chases a phantom: RX CSI frames and TX heartbeats are unicast
to `CSI_UDP_PORT` (`5005`, the archive port); the TX measurement broadcast goes
to `5007` deliberately, so a broadcast flood can never be mistaken for archived
records.

| board | env | `CSI_NODE_ID` | display |
| --- | --- | --- | --- |
| classic ESP32 | `esp32_tx` | 255 | OLED |
| S3-N16R8 #1 | `esp32s3_rx1` | 1 | none (`-D NO_OLED`) |
| S3-N16R8 #2 | `esp32s3_rx2` | 2 | none (`-D NO_OLED`) |

## Order of operations

1. Build and flash the TX first: `pio run -e esp32_tx -t upload`. Serial must
   show `TX_BEACON_START <ssid>`; the host now has a network to join.
2. Join the host to that AP, set `CSI_HOST_IP` to the address it got, and
   rebuild/reflash the RX boards if it changed.
3. Flash the RX nodes one at a time (`-e esp32s3_rx1`, then `-e esp32s3_rx2`).
   Each must show `CSI_UDP_START <ip>:5005`. S3 logs come over USB CDC at
   115200, not UART.
4. Start the recorder **before** the acceptance run, so the first frames are
   not the ones you lose:

       python3 tools/csi_aggregate.py --chips 1,2 --nodes 1,2 \
           --duration 600 -o captures/m0-$(date +%F)

A node line should appear within a few seconds. If a node is silent, see
triage below before restarting anything.

## Reading the summary

One line per node, then totals:

    node 1 (chip 2): 3150 frames, 2 seq gaps, 0 duplicates, txmac <bssid>, clock -40.8 ppm rms 0.06 ms
    node 2 (chip 2): 3140 frames, 1 seq gaps, 0 duplicates, txmac <bssid>, clock +27.5 ppm rms 0.20 ms
    2 node(s), 6000 merged rows at dt=0.05s
    frames=6290 malformed=0 tx_frames_ignored=600

- **frames** is a measurement, not an assumption. `CsiCollector` also pings the
  gateway every 100 ms, so the observed rate sits *above* the softAP broadcast
  rate (`1/T`). Report frames/s; do not report `1/T` as the rate.
- **seq gaps** are UDP loss plus anything the firmware dropped; sustained gaps
  mean the radio link is marginal, not that the host is wrong.
- **txmac** must be the same for every node and must not change during the run.
  A change means that RX re-associated to another AP and is measuring the wrong
  link (issue #16 is the recovery path).
- **clock ppm / rms**: rms is the honest quality figure (sub-millisecond is
  good). ppm is `(host-per-node slope - 1)`, so a node that runs fast reads
  negative; it only means anything over a long, low-jitter window.
- **malformed** must be `0`. Anything else is the host refusing frames —
  `node_id 0` (unset `CSI_NODE_ID`), an unexpected chip id, or a truncated
  datagram.
- **tx_frames_ignored** counts TX heartbeats (1 Hz, length 0). ~600 over ten
  minutes; `0` means the TX unicast path is broken.

## Abort rules (frozen)

Stop the run and fix the topology rather than interpreting the data if any of
these hold:

- 5 minutes of TX broadcast with zero TX-MAC frames on any RX;
- any RX sustained below 5 frames/s;
- any `TX-MAC CHANGES` in the summary;
- `malformed` above 0.

## Artifacts to keep together

Per run, in `captures/m0-<date>/`:

- `node-1.jsonl`, `node-2.jsonl` — raw v2 records, unmodified;
- `merged.csv` — long format, one row per (bucket, node), gaps explicit;
- the aggregator's stdout (the summary above);
- one serial log per node, including the boot lines.

`captures/` is ignored by git; these are local evidence, not commits.

## Re-analysing afterwards

The saved JSONL replays offline through the same pipeline:

    python3 tools/csi_aggregate.py --replay captures/m0-<date>/node-*.jsonl \
        --dt 0.05 -o captures/m0-<date>/rerun

A ten-minute two-node replay takes about half a minute. Per-bucket `frames`
counts can move by ±1 between runs when a timestamp lands exactly on the grid
boundary; amplitudes and gap accounting do not.

## Triage

| symptom | likely cause |
| --- | --- |
| node silent, serial says "Joined uplink only" | TX softAP was down at boot; power-cycle the RX after the TX is up (issue #16) |
| node silent, no CSI line at all | host not on the TX AP, or `CSI_HOST_IP` points at another network |
| `TX-MAC CHANGES` in the summary | RX re-associated to the home AP mid-run |
| `malformed` climbing at the frame rate | board built without `CSI_NODE_ID`; the build should now fail instead |
| `tx_frames_ignored=0` | TX heartbeat socket failed; check the TX serial log |
| clock `fallbacks` rising | node clock stepped (NTP) or reset; arrival time is being used instead |
