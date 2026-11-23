# Session handover — 2026-11-20 (pi)

Next session: start here. This file is the living handover; update it at the
next handoff. Firmware behavior and commands live in `README.md`; repo rules
in `AGENTS.md`; M0 architecture in `docs/csi-multi-node-design.md`.

## Where things stand (updated 2026-11-23)

- `dev` = `f744ed4`: PR #15 (firmware v2+TX+S3, incl. Hermes review fixes)
  + PR #14 (host v2 parser+aggregator) + PR #17 (M0 runbook) ALL MERGED.
  Former branch `feat/csi-v2-tx-rx` is done; work happens on `dev` or new
  `feat/*` branches now.
- Hermes owns `feat/csi-aggregator` (separate worktree
  `../sketch_jul29a-agg`): PR #14 (host v2 parser) OPEN / MERGEABLE;
  aggregator + M0 run analysis still in progress on that branch.
- v2 frame layout is FROZEN (`af9e94a`): 32-byte header, `rx_ts_us` (RX-side
  timestamp), flags bit0=first-word-invalid / bits1-2=bandwidth / bit3=role,
  `node_id` via `-D CSI_NODE_ID` (255=TX, 0=unset), C5 hard `#error`.
- Hardware inventory (user-confirmed): 1x classic ESP32 + 2x ESP32-S3-N16R8
  (16 MB flash + 8 MB OPI-PSRAM, **no external OLED**). ESP32-C5 is
  deferred — pinned `espressif32@7.0.1`/Arduino core has no C5 definitions.
- Topology (user decision): TX = classic ESP32 (`esp32_tx`, node 255,
  with OLED); RX = 2x S3 (`esp32s3_rx1` node 1, `esp32s3_rx2` node 2,
  headless via `-D NO_OLED` which makes the display a no-op). `pio run`
  builds all three; all three SUCCESS locally.
- Single-link walk-through experiment is PAUSED until M0 validates; the M0
  scripted protocol (metronome, guard bands) will supersede hand-waving.

## TODO for next session (in order)

1. ~~Collect Hermes review on PR #15, merge~~ DONE (merged 2026-11-20;
   #14 and runbook #17 also in). Next: flash + M0 run (needs user hardware).
2. **Flash M0**: fill `CSI_TX_SSID/PASSWORD` in gitignored `secrets.h`
   (never commit); join the host to the `csi-tx` softAP; set `CSI_HOST_IP`
   to the host's address on that network. Flash TX first, then RX1, verify
   TX heartbeats + RX CSI frames in the aggregator, then add RX2.
   (S3 shows as USB CDC; if no port appears, hold BOOT + tap RESET.)
3. **M0 10-min acceptance run**: rate within X% of 1/T, per-node loss, clock
   skew stable. ABORT (frozen): 5-min TX broadcast with zero TX-MAC frames
   on any RX, or sustained < 5 fps/RX → stop, revisit topology before any
   aggregator work.
4. **Scripted posture protocol** (only after M0 holds): 20–30 s/class,
   metronome-cued, 5 s guard bands dropped; N=1 vs N=2 ablation as headline.
5. **Leftovers**: P0 capacity-visibility + `detect_params=None` guard
   (after Hermes cleanup lands); `main` release later via `dev`→`main`
   PR + tag (user's standing plan).
6. Update this file at the next handoff.

## Watch-outs

- `include/secrets.h` is gitignored and required to build; never commit it.
- Beacons use UDP 5007 (not the 5005 archive port); heartbeats are v2
  frames with `len=0`, role TX, node 255.
- `tools/csi_udp_read.py` still validates v1 captures; the v2 host path is
  Hermes's PR #14 — do not duplicate it here.
