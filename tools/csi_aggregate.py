#!/usr/bin/env python3
"""Merge CSI1 streams from several RX nodes into one timeline.

M0 host half of docs/csi-multi-node-design.md. Each RX node sends its own
UDP stream (same host and port; the frame carries `node_id`), and this tool:

- keys frames by `(node_id, sequence)` so duplicates and gaps are explicit;
- fits an affine map from each node's `rx_ts_us` onto the host clock, so the
  merged timeline is not hostage to UDP arrival jitter;
- resamples the corrected times onto a fixed grid (`--dt`), emitting an
  explicit `is_gap` row per node and bucket instead of interpolating through
  a hole;
- writes one JSONL per node (the raw records, unchanged) plus a long-format
  merged CSV.

Long CSV columns: `t_unix, node_id, amp_mean, amp_std, rssi_dbm, frames,
is_gap`. One row per (bucket, node); `frames == 0` and `is_gap == 1` mean the
node delivered nothing in that bucket. Rows are ordered by time, then node.

Examples:
  python3 tools/csi_aggregate.py --port 5005 -o captures/m0
  python3 tools/csi_aggregate.py --replay captures/n1.jsonl captures/n2.jsonl \\
      -o /tmp/m0 --dt 0.05
"""

import argparse
import csv
import json
import math
import socket
import statistics
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from csi_frame import amplitude_spectrum
from csi_read import MAX_FRAME_SIZE, NODE_ID_TX, frame_to_record, parse_frame

DEFAULT_DT = 0.05
DEFAULT_FIT_WINDOW_S = 120.0
MIN_FIT_PAIRS = 20
TRIM_FRACTION = 0.10

CSV_COLUMNS = [
    "t_unix",
    "node_id",
    "amp_mean",
    "amp_std",
    "rssi_dbm",
    "frames",
    "is_gap",
]


def fit_clock(pairs, trim=TRIM_FRACTION):
    """Least-squares affine map node seconds -> host seconds.

    `pairs` is an iterable of (node_seconds, host_seconds). Returns
    (slope, intercept, rms_ms, used). The worst `trim` fraction of residuals
    is dropped once before the final fit, so a scheduling hiccup or a
    reordered datagram cannot tilt the slope.
    """
    pts = [(float(x), float(y)) for x, y in pairs]
    if len(pts) < 2:
        raise ValueError("need at least two clock pairs")

    def solve(sample):
        n = len(sample)
        mean_x = sum(x for x, _ in sample) / n
        mean_y = sum(y for _, y in sample) / n
        sxx = sum((x - mean_x) ** 2 for x, _ in sample)
        sxy = sum((x - mean_x) * (y - mean_y) for x, y in sample)
        if sxx == 0:
            return 1.0, mean_y - mean_x
        slope = sxy / sxx
        return slope, mean_y - slope * mean_x

    slope, intercept = solve(pts)
    if trim > 0 and len(pts) >= 8:
        residuals = sorted(
            ((abs(y - (slope * x + intercept)), x, y) for x, y in pts),
            key=lambda item: item[0],
        )
        keep = max(2, int(round(len(residuals) * (1.0 - trim))))
        kept = [(x, y) for _, x, y in residuals[:keep]]
        slope, intercept = solve(kept)
        pts = kept

    rms = math.sqrt(
        sum((y - (slope * x + intercept)) ** 2 for x, y in pts) / len(pts)
    )
    return slope, intercept, rms * 1000.0, len(pts)


def seq_gap(previous, current):
    """Forward gap between two wrapped uint32 sequences (0 = contiguous)."""
    if previous is None:
        return 0
    diff = (int(current) - int(previous)) & 0xFFFFFFFF
    return diff - 1 if 0 < diff < 0x80000000 else 0


def bucket_index(t_unix, dt):
    """Fixed-grid bucket for a timestamp; negative times keep their sign.

    The tiny epsilon absorbs float division error (10.2 / 0.1 evaluates to
    101.99999999999999), which would otherwise push a boundary frame into
    the previous bucket.
    """
    if dt <= 0:
        raise ValueError("dt must be positive")
    return int(math.floor(float(t_unix) / dt + 1e-9))


class NodeStream:
    """Per-node bookkeeping: dedupe, gaps, clock fit, bucket aggregation."""

    def __init__(self, node_id, dt, fit_window_s=DEFAULT_FIT_WINDOW_S,
                 clock=None):
        self.node_id = int(node_id)
        self.dt = float(dt)
        self.fit_window_s = float(fit_window_s)
        self.clock = clock or LinearClock(fit_window_s)
        self.last_seq = None
        self.seen = deque(maxlen=256)
        self.frames = 0
        self.duplicates = 0
        self.gaps = 0
        self.buckets = {}

    def add(self, sequence, payload, first_word_invalid, rssi, host_time,
            node_time_s=None):
        """Register one frame; returns False when it is a duplicate."""
        seq = int(sequence) & 0xFFFFFFFF
        if seq in self.seen:
            self.duplicates += 1
            return False
        self.seen.append(seq)

        self.gaps += seq_gap(self.last_seq, seq)
        self.last_seq = seq

        if node_time_s is not None:
            self.clock.add(node_time_s, host_time)
            corrected = self.clock.map(node_time_s, fallback=host_time)
        else:
            corrected = host_time

        spectrum = amplitude_spectrum(payload, first_word_invalid)
        index = bucket_index(corrected, self.dt)
        bucket = self.buckets.setdefault(
            index, {"amp": [], "rssi": [], "frames": 0})
        bucket["amp"].append(statistics.fmean(spectrum))
        bucket["amp_std"] = (
            statistics.pstdev(spectrum) if len(spectrum) > 1 else 0.0)
        bucket["rssi"].append(int(rssi))
        bucket["frames"] += 1
        self.frames += 1
        return True

    def flush_before(self, t_unix):
        """Yield (t_unix, row_dict) for buckets strictly older than t_unix."""
        cutoff = bucket_index(t_unix, self.dt)
        for index in sorted(i for i in self.buckets if i < cutoff):
            yield from self._emit(index)

    def flush_all(self):
        for index in sorted(self.buckets):
            yield from self._emit(index)

    def _emit(self, index):
        bucket = self.buckets.pop(index)
        amp_means = bucket["amp"]
        yield index * self.dt, {
            "node_id": self.node_id,
            "amp_mean": statistics.fmean(amp_means),
            "amp_std": bucket.get("amp_std", 0.0),
            "rssi_dbm": statistics.fmean(bucket["rssi"]),
            "frames": bucket["frames"],
            "is_gap": 0,
        }

    def gap_rows(self, start_index, end_index):
        """Explicit holes so downstream code cannot mistake silence for data."""
        for index in range(start_index, end_index):
            if index in self.buckets:
                continue
            yield index * self.dt, {
                "node_id": self.node_id,
                "amp_mean": None,
                "amp_std": None,
                "rssi_dbm": None,
                "frames": 0,
                "is_gap": 1,
            }


class LinearClock:
    """Sliding affine map from node time to host time."""

    def __init__(self, window_s=DEFAULT_FIT_WINDOW_S, min_pairs=MIN_FIT_PAIRS,
                 max_offset_s=5.0):
        self.window_s = float(window_s)
        self.min_pairs = int(min_pairs)
        self.max_offset_s = float(max_offset_s)
        self.pairs = deque()
        self.slope = 1.0
        self.intercept = 0.0
        self.rms_ms = 0.0
        self.fitted_pairs = 0
        self.ready = False
        self.fallbacks = 0

    def add(self, node_s, host_s):
        self.pairs.append((float(node_s), float(host_s)))
        newest = self.pairs[-1][0]
        while self.pairs and self.pairs[0][0] < newest - self.window_s:
            self.pairs.popleft()
        if len(self.pairs) >= self.min_pairs:
            self.slope, self.intercept, self.rms_ms, self.fitted_pairs = (
                fit_clock(self.pairs))
            self.ready = True

    def map(self, node_s, fallback):
        """Node seconds -> host-clock seconds, or `fallback` when untrusted.

        Until the fit has enough pairs, node and host clocks share no epoch
        (they differ by boot time, tens of thousands of seconds), so arrival
        time is the only safe timeline. After that, a corrected time that
        disagrees with arrival by more than `max_offset_s` is treated as a
        clock reset rather than trusted.
        """
        if not self.ready:
            return float(fallback)
        corrected = self.slope * float(node_s) + self.intercept
        if abs(corrected - float(fallback)) > self.max_offset_s:
            self.fallbacks += 1
            return float(fallback)
        return corrected

    @property
    def ppm(self):
        return (self.slope - 1.0) * 1e6


def record_from_jsonl(record):
    """Normalise one archived JSONL record to the fields the aggregator needs."""
    node_id = record.get("node_id")
    if node_id in (None, 0):
        raise ValueError("record has no node_id (v1 capture or unset CSI_NODE_ID)")
    payload = bytes.fromhex(record.get("csi_payload_hex", ""))
    stamp = record.get("captured_at_utc")
    if stamp:
        host_time = datetime.fromisoformat(stamp).timestamp()
    else:
        host_time = float(record["unix_time"])
    rx_ts_us = record.get("rx_ts_us")
    return {
        "node_id": int(node_id),
        "sequence": int(record["sequence"]),
        "payload": payload,
        "first_word_invalid": bool(record.get("first_word_invalid", False)),
        "rssi": int(record.get("rssi_dbm", 0)),
        "host_time": host_time,
        "node_time_s": (rx_ts_us / 1e6) if rx_ts_us else None,
    }


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in CSV_COLUMNS})


def merged_rows(streams, start_index, end_index, dt):
    """Long-format rows for a bucket range, gaps included, time-ordered."""
    for index in range(start_index, end_index):
        t_unix = index * dt
        for stream in streams:
            bucket = stream.buckets.get(index)
            if bucket is None:
                yield t_unix, {
                    "t_unix": t_unix,
                    "node_id": stream.node_id,
                    "amp_mean": None,
                    "amp_std": None,
                    "rssi_dbm": None,
                    "frames": 0,
                    "is_gap": 1,
                }
            else:
                amp_means = bucket["amp"]
                yield t_unix, {
                    "t_unix": t_unix,
                    "node_id": stream.node_id,
                    "amp_mean": statistics.fmean(amp_means),
                    "amp_std": bucket.get("amp_std", 0.0),
                    "rssi_dbm": statistics.fmean(bucket["rssi"]),
                    "frames": bucket["frames"],
                    "is_gap": 0,
                }


def replay(paths, dt):
    """Build node streams from archived JSONL files (no hardware needed)."""
    streams = {}
    for path in paths:
        with Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = record_from_jsonl(json.loads(line))
                except (ValueError, KeyError) as error:
                    print(f"{path}: skipped record ({error})", file=sys.stderr)
                    continue
                stream = streams.get(record["node_id"])
                if stream is None:
                    stream = streams[record["node_id"]] = NodeStream(
                        record["node_id"], dt)
                stream.add(
                    sequence=record["sequence"],
                    payload=record["payload"],
                    first_word_invalid=record["first_word_invalid"],
                    rssi=record["rssi"],
                    host_time=record["host_time"],
                    node_time_s=record["node_time_s"],
                )
    return streams


def run_live(listen, port, dt, duration, output_dir, expected_nodes):
    """Receive all RX streams on one socket; node_id comes from the frame."""
    streams = {}
    writers = {}
    frame_total = 0
    malformed = 0
    seen_tx = 0
    started = time.monotonic()
    deadline = started + duration if duration else None
    last_flush = started

    def writer_for(node_id):
        handle = writers.get(node_id)
        if handle is None:
            path = output_dir / f"node-{node_id}.jsonl"
            handle = writers[node_id] = path.open("a", encoding="utf-8")
        return handle

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind((listen, port))
            sock.settimeout(0.25)
            while deadline is None or time.monotonic() < deadline:
                try:
                    datagram, addr = sock.recvfrom(MAX_FRAME_SIZE + 1)
                except socket.timeout:
                    continue
                host_time = time.time()
                try:
                    frame = parse_frame(datagram)
                except ValueError as error:
                    malformed += 1
                    print(f"Ignored malformed datagram from {addr[0]}: {error}",
                          file=sys.stderr)
                    continue
                if frame.role == "tx" or frame.node_id == NODE_ID_TX:
                    seen_tx += 1
                    continue
                if expected_nodes and frame.node_id not in expected_nodes:
                    print(f"Warning: unexpected node_id {frame.node_id} from "
                          f"{addr[0]}", file=sys.stderr)
                stream = streams.get(frame.node_id)
                if stream is None:
                    stream = streams[frame.node_id] = NodeStream(
                        frame.node_id, dt)
                stream.add(
                    sequence=frame.sequence,
                    payload=frame.payload,
                    first_word_invalid=frame.first_word_invalid,
                    rssi=frame.rssi,
                    host_time=host_time,
                    node_time_s=((frame.rx_ts_us / 1e6)
                                 if frame.rx_ts_us else None),
                )
                record = frame_to_record(frame)
                record["sender_ip"] = addr[0]
                record["sender_port"] = addr[1]
                writer_for(frame.node_id).write(json.dumps(record) + "\n")
                frame_total += 1
                if time.monotonic() - last_flush >= 1.0:
                    for handle in writers.values():
                        handle.flush()
                    last_flush = time.monotonic()
    finally:
        for handle in writers.values():
            handle.close()

    return streams, frame_total, malformed, seen_tx


def summarise(streams, output_dir, dt):
    lines = []
    for node_id in sorted(streams):
        stream = streams[node_id]
        lines.append(
            f"node {node_id}: {stream.frames} frames, {stream.gaps} seq gaps, "
            f"{stream.duplicates} duplicates, "
            f"clock {stream.clock.ppm:+.1f} ppm rms {stream.clock.rms_ms:.2f} ms"
            f"{f', {stream.clock.fallbacks} arrival fallbacks' if stream.clock.fallbacks else ''}"
        )
    rows = 0
    if streams:
        indices = [i for s in streams.values() for i in s.buckets]
        if indices:
            rows = (max(indices) - min(indices) + 1) * len(streams)
    lines.append(f"{len(streams)} node(s), {rows} merged rows at dt={dt}s")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("-p", "--port", type=int, default=5005)
    parser.add_argument("--replay", nargs="+", type=Path, default=None,
                        help="one or more v2 JSONL captures instead of UDP")
    parser.add_argument("--dt", type=float, default=DEFAULT_DT,
                        help=f"resample grid in seconds (default {DEFAULT_DT})")
    parser.add_argument("--nodes", default="",
                        help="expected node ids, comma separated (warns on others)")
    parser.add_argument("--duration", type=float, default=0,
                        help="stop after N seconds of live capture (0 = Ctrl-C)")
    parser.add_argument("-o", "--output-dir", type=Path, default=None,
                        help="output directory (default captures/agg-<UTC>)")
    args = parser.parse_args(argv)

    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.dt <= 0:
        parser.error("--dt must be positive")
    if args.duration < 0:
        parser.error("--duration must be 0 or greater")
    expected = {int(part) for part in args.nodes.split(",") if part.strip()}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir or Path("captures") / f"agg-{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.replay:
        try:
            streams = replay(args.replay, args.dt)
        except OSError as error:
            print(f"cannot read replay input: {error}", file=sys.stderr)
            return 2
        frame_total = sum(s.frames for s in streams.values())
        malformed = seen_tx = 0
    else:
        print(f"Aggregating CSI1 v2 streams on {args.listen}:{args.port}")
        print("Press Ctrl-C to stop.")
        try:
            streams, frame_total, malformed, seen_tx = run_live(
                args.listen, args.port, args.dt, args.duration,
                output_dir, expected)
        except KeyboardInterrupt:
            print("\nStopped by user.")
            streams, frame_total, malformed, seen_tx = {}, 0, 0, 0
        except OSError as error:
            print(f"aggregator failed: {error}", file=sys.stderr)
            return 2

    if not streams:
        print("No frames aggregated; nothing written.", file=sys.stderr)
        return 1

    indices = [i for s in streams.values() for i in s.buckets]
    start, end = min(indices), max(indices) + 1
    csv_path = output_dir / "merged.csv"
    write_csv(csv_path, (row for _, row in merged_rows(
        list(streams.values()), start, end, args.dt)))
    print(summarise(streams, output_dir, args.dt))
    print(f"frames={frame_total} malformed={malformed} tx_frames_ignored={seen_tx}")
    print(f"wrote {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
