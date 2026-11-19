# Multi-node CSI: design decisions and target architecture

Status: proposal for review. No firmware changes have been made against it.
Owner of `src/` and the frame format: pi. Author of this draft: Hermes.
Date: 2026-11-19. Base: `dev` = `e6ff921`.

This document fixes the decisions that block implementation. It is not an
implementation spec; the ticket-level breakdown comes after sign-off.

## Decisions (settled with the project owner)

| # | Question | Decision |
|---|---|---|
| D1 | Packet source for the nodes | Dedicated **TX node** + N RX nodes. Fixed, controllable packet interval instead of ping-driven traffic. |
| D2 | Channel and bandwidth | **One fixed 2.4 GHz channel, 20 MHz, LLTF** (as today). No HT-LTF, no channel hopping. Comparability over subcarrier count. |
| D3 | Ground truth | **Scripted protocol + metronome** (timed posture segments), no camera, no wearable in the first phase. |
| D4 | Success definition | **Coarse posture is the primary target** (3-4 classes). Presence/activity is a by-product, not the goal. |
| D5 | First experiment size | **Two RX nodes** for drift/loss/rate validation, then extend. |

Consequences worth stating plainly:

- D3 means every label is a time interval, so the metronome and the node clocks
  are the measurement instrument. Time alignment is not a nice-to-have here; a
  label that is 2 s late silently trains a wrong model.
- D4 puts posture ahead of presence, which is the harder half. If Stage 1 shows
  the second node adds no separable information, the honest outcome is to report
  that and stop, not to keep adding nodes.
- D2 accepts 64 subcarriers per link as the ceiling. Multi-node buys geometric
  diversity, not bandwidth.

## Current state (verified in this repo)

Firmware (`src/csi/CsiCollector.cpp`):

- One station associated to an AP, pinging the gateway every 100 ms
  (`kPingIntervalMs = 100`), keeping only AP-BSSID packets.
- CSI: `lltf_en = true`, `htltf_en = false`, `stbc_htltf2_en = false`,
  `ltf_merge_en = true`, `channel_filter_en = true`, `manu_scale = true`
  (lines 205-213). 128-byte payload, 64 subcarriers.
- 24-byte packed header: `magic CSI1 / version / chip / len / seq / rssi /
  channel / firstWordInvalid / reserved / mac[6] / drops`. No node identity, no
  radio timestamp. Nodes are distinguishable only by UDP source IP.
- Measured on captures: 8.8-13.3 fps, irregular; one capture accumulated 175
  sequence gaps over 325 rows (36.8 s).

Host (`tools/`): JSONL archive, CSV time log, PNG plots, live plot with an
incremental detector, offline detector with `--mark` scoring. Thresholds in
`tools/csi_detect.json`.

## Gaps this design has to close

1. **Identity and time.** Without `node_id` and a node-side timestamp, N nodes
   cannot be merged into one timeline, and D3's scripted labels cannot be
   aligned to better than host arrival jitter.
2. **Sampling regularity.** Ping-driven sampling is neither regular nor
   controllable, and N boards pinging at once contend with each other. D1
   exists to fix this.
3. **Physical layer.** Single antenna, LLTF only. Fine for amplitude features,
   hostile to phase. D2 freezes this deliberately for the first phase.
4. **Evaluation.** No ground-truth pipeline, no session-level split, no
   cross-session holdout. Without it, any accuracy figure is unfalsifiable.

## Target architecture

### Node roles (D1)

- **TX node**: a dedicated board that emits a fixed-rate stream. It runs an AP
  (softAP) and sends a UDP broadcast every `T` ms, so RX boards associated to it
  receive a packet at a known cadence. The payload is irrelevant; the frame
  presence is the measurement.
- **RX nodes**: stations associated to the TX node's AP, pinned to the TX
  channel, CSI filtered to the TX node's MAC, each forwarding frames to the host.
- The AP uplink is not used for CSI. Keeping the AP out of the measurement
  removes the traffic-dependent sampling that exists today.

### Frame v2 (D1, D3)

Version bump, keep v1 readable for archived captures:

| Field | Bytes | Notes |
|---|---|---|
| magic `CSI1` | 4 | unchanged |
| version | 1 | `2` |
| chip | 1 | unchanged |
| len | 2 | unchanged |
| node_id | 1 | RX node identity, assigned per board |
| flags | 1 | first-word-invalid, bandwidth, TX/RX role |
| seq | 4 | per node, wraps |
| tx_ts_us | 8 | `esp_timer_get_time()` at RX capture, monotonic |
| rssi | 1 | unchanged |
| channel | 1 | unchanged |
| drops | 2 | unchanged |
| mac[6] | 6 | TX (peer) MAC |

`node_id` and `tx_ts_us` are the two fields that unlock everything else. The
header grows past 24 bytes, so `src/csi/CsiCollector.cpp`, `tools/csi_read.py`
and `tools/csi_udp_read.py` must move in lockstep (see AGENTS.md).

### Transport and aggregation

Each RX sends its own UDP stream; one host aggregator owns the merge:

- key `(node_id, seq)` for dedupe and gap accounting per node;
- estimate per-node clock offset/skew by fitting `host_arrival - tx_ts_us`
  over a sliding window (least squares on a robust subset), and store both the
  raw node time and the corrected time;
- resample to a common grid (fixed `dt`, forward-fill up to a gap threshold,
  mark gaps explicitly rather than interpolating through them);
- emit per-node JSONL (unchanged format plus the v2 fields) and one merged CSV.

### Sampling target

To be measured, not assumed. Proposed acceptance for M0: sustained rate within
X% of the configured `1/T`, per-node loss under Y%, and clock skew stable over
10 minutes. Suggest starting at `T = 20 ms` (50 Hz) and dropping to the lowest
rate that still supports the posture window; the ESP32 CSI callback and UDP path
must be measured before committing to any figure.

### Features and model (D4)

- Per-link amplitude windows (1-2 s, step 0.25 s): per-subcarrier mean/std/median
  deviation, plus the existing energy feature.
- Fusion: early (concatenate per-link features) vs late (per-link decision,
  majority/score average). Report both; early fusion is the one that can use
  cross-link correlation.
- Classes: `empty / standing / sitting / lying` (add `walking` only if the
  protocol makes it separable).
- Baseline to beat: the current single-link detector on the same protocol.

### Evaluation protocol (D3)

- Scripted segments of 20-30 s per class, metronome-cued, with a 5 s guard band
  dropped at each boundary so a label never straddles a transition.
- Split by **session**, not by window: train on sessions 1-3, test on session 4,
  and report a held-out session as the headline number.
- Metrics: per-class recall, confusion matrix, false-alarm rate per hour for
  `empty`, and the same figure for N=1 vs N=2 so the second node's contribution
  is measurable.
- Report the ablation, not just the best number.

## Milestones

| M | Content | Acceptance |
|---|---|---|
| M0 | TX node + 2 RX, v2 frames, aggregator | 10 min run: rate/loss/skew within the stated bounds; merged CSV loads into the existing tools |
| M1 | Presence/activity on merged data | Single-node baseline reproduced; N=2 ablation reported |
| M2 | 4-class posture | Confusion matrix on a held-out session; N=1 vs N=2 ablation |
| M3 | Respiration | Out of scope until M2 holds |

## Non-goals (explicit)

- **Heartbeat.** Sub-centimetre chest displacement needs phase sanitisation,
  CFO/SFO handling and stable sampling well above what LLTF amplitude gives.
  Amplitude-only, 20 MHz, single-antenna links do not carry it. Respiration is
  the defensible first vital sign, and only after M2.
- Fine-grained pose / skeleton reconstruction: requires multi-antenna MIMO and
  orders of magnitude more labelled data (see `docs/csi-human-sensing.md`).
- On-device classification: everything stays host-side for now.

## Risks

- **Rate ceiling.** ESP32 CSI callback + UDP may not sustain 50 Hz with 3 nodes;
  M0 exists to find the real number before anything is built on it.
- **UDP loss.** No retransmission; sequence gaps must be first-class in the
  aggregator, and the resampler must not interpolate across them silently.
- **Clock drift.** `esp_timer` is per-board; without the offset fit, labels drift.
- **Label timing.** With D3, a late metronome segment is a wrong label, not noise.
- **Overfitting.** Small scripted datasets plus session-level splits: report
  held-out results only.

## Next actions

1. Review and sign off this document (pi owns `src/` and the frame format).
2. Ticket M0 only: firmware v2 header + TX mode, aggregator, two-node run.
3. Nothing in `src/` moves before step 1.
