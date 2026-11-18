#!/usr/bin/env python3
"""Record time vs CSI amplitude to CSV.

Listens for the same CSI1 UDP datagrams as tools/csi_udp_read.py, converts
each frame to an amplitude spectrum (see tools/csi_frame.amplitude_spectrum),
and appends one row per frame:

  captured_at_utc, unix_time, sequence, seq_gap, rssi_dbm, channel,
  source_mac, n_subcarriers, amp_mean, amp_std, amp_min, amp_max,
  fw_drops, sender_ip

With --per-subcarrier, one scNN column per subcarrier is appended (width is
fixed by the first frame; later size changes are truncated/padded).

Timing semantics match tools/csi_udp_read.py: the timestamp is when THIS
host handled the datagram, not the radio reception time. UDP loss shows up
as seq_gap > 0. Use --replay to convert an existing JSONL capture
(e.g. one archived with tools/csi_live_plot.py -o) without needing the board.

Examples:
  python3 tools/csi_time_log.py --port 5005
  python3 tools/csi_time_log.py --port 5005 -o captures/amp.csv --per-subcarrier
  python3 tools/csi_time_log.py --replay captures/csi-20261108T014649Z.jsonl -o /tmp/amp.csv
"""

import argparse
import csv
import json
import socket
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from csi_frame import amplitude_spectrum
from csi_udp_read import parse_frame_datagram

BASE_COLUMNS = [
    "captured_at_utc",
    "unix_time",
    "sequence",
    "seq_gap",
    "rssi_dbm",
    "channel",
    "source_mac",
    "n_subcarriers",
    "amp_mean",
    "amp_std",
    "amp_min",
    "amp_max",
    "fw_drops",
    "sender_ip",
]


def spectrum_stats(spectrum):
    """Summarize one amplitude spectrum; stdlib only, no numpy needed."""
    values = [float(v) for v in spectrum]
    if not values:
        raise ValueError("empty spectrum")
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "std": statistics.pstdev(values),
        "min": min(values),
        "max": max(values),
    }


class AmpRecorder:
    """Builds CSV rows and tracks sequence gaps across frames."""

    def __init__(self, per_subcarrier=False):
        self.per_subcarrier = per_subcarrier
        self.last_seq = None
        self.sc_width = 0  # fixed by the first frame when per_subcarrier
        self.columns = list(BASE_COLUMNS)

    def make_row(self, unix_time, captured_at_utc, sequence, rssi_dbm,
                 channel, source_mac, fw_drops, sender_ip, spectrum):
        stats = spectrum_stats(spectrum)
        if self.last_seq is None:
            gap = 0
        else:
            diff = (int(sequence) - int(self.last_seq)) & 0xFFFFFFFF
            gap = diff - 1 if 0 < diff < 0x80000000 else 0
        self.last_seq = int(sequence)

        if self.per_subcarrier and not self.sc_width:
            self.sc_width = stats["n"]
            self.columns = list(BASE_COLUMNS) + [
                f"sc{i:02d}" for i in range(self.sc_width)
            ]
        row = {
            "captured_at_utc": captured_at_utc,
            "unix_time": f"{unix_time:.3f}",
            "sequence": int(sequence),
            "seq_gap": gap,
            "rssi_dbm": int(rssi_dbm),
            "channel": int(channel),
            "source_mac": source_mac,
            "n_subcarriers": stats["n"],
            "amp_mean": f"{stats['mean']:.3f}",
            "amp_std": f"{stats['std']:.3f}",
            "amp_min": f"{stats['min']:.3f}",
            "amp_max": f"{stats['max']:.3f}",
            "fw_drops": int(fw_drops),
            "sender_ip": sender_ip,
        }
        if self.per_subcarrier:
            amps = [float(v) for v in spectrum]
            for i in range(self.sc_width):
                row[f"sc{i:02d}"] = f"{amps[i]:.3f}" if i < len(amps) else ""
        return row


class CsvSink:
    """CSV writer with a lazy header (scNN width is fixed by frame one)."""

    def __init__(self, path):
        self.handle = path.open("w", newline="", encoding="utf-8")
        self.writer = None
        self.rows = 0

    def write(self, recorder, row):
        if self.writer is None:
            self.writer = csv.DictWriter(self.handle,
                                         fieldnames=recorder.columns)
            self.writer.writeheader()
        self.writer.writerow(row)
        self.rows += 1

    def close(self):
        self.handle.close()


def default_output_path():
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("captures") / f"csi-amp-{timestamp}.csv"


def run_udp(recorder, sink, listen, port, chip, count, timeout,
            progress_every=50):
    """Receive live datagrams; returns (frames, malformed)."""
    from csi_read import HEADER, MAX_LEN

    frames = 0
    malformed = 0
    started_at = time.monotonic()
    deadline = started_at + timeout if timeout else None
    last_report = started_at
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((listen, port))
        sock.settimeout(0.25)
        while count == 0 or frames < count:
            if deadline is not None and time.monotonic() >= deadline:
                break
            try:
                datagram, addr = sock.recvfrom(HEADER.size + MAX_LEN + 1)
            except socket.timeout:
                continue
            try:
                frame = parse_frame_datagram(datagram, chip)
                spectrum = amplitude_spectrum(frame.payload,
                                              frame.first_word_invalid)
            except ValueError as error:
                malformed += 1
                print(f"Ignored malformed datagram: {error}", file=sys.stderr)
                continue
            unix_time = time.time()
            row = recorder.make_row(
                unix_time=unix_time,
                captured_at_utc=datetime.fromtimestamp(
                    unix_time, tz=timezone.utc).isoformat(),
                sequence=frame.sequence,
                rssi_dbm=frame.rssi,
                channel=frame.channel,
                source_mac=":".join(f"{b:02x}" for b in frame.mac),
                fw_drops=frame.drops,
                sender_ip=addr[0],
                spectrum=spectrum,
            )
            sink.write(recorder, row)
            frames += 1
            now = time.monotonic()
            if frames % progress_every == 0 or now - last_report >= 10:
                rate = frames / max(now - started_at, 1e-9)
                print(f"... {frames} frames ({rate:.1f}/s), "
                      f"last amp_mean={row['amp_mean']}", file=sys.stderr)
                last_report = now
    return frames, malformed


def run_replay(recorder, sink, path, progress_every=200):
    """Convert a JSONL capture (csi_udp_read.py format) to CSV rows."""
    import binascii

    frames = 0
    malformed = 0
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                payload = bytes.fromhex(rec.get("csi_payload_hex",
                                                rec.get("payload_hex", "")))
                spectrum = amplitude_spectrum(
                    payload, rec.get("first_word_invalid", False))
                try:
                    unix_time = datetime.fromisoformat(
                        rec["captured_at_utc"]).timestamp()
                except (KeyError, ValueError):
                    unix_time = time.time()
            except (ValueError, binascii.Error, KeyError) as error:
                malformed += 1
                print(f"Ignored malformed record: {error}", file=sys.stderr)
                continue
            row = recorder.make_row(
                unix_time=unix_time,
                captured_at_utc=rec.get(
                    "captured_at_utc",
                    datetime.fromtimestamp(unix_time,
                                           tz=timezone.utc).isoformat()),
                sequence=rec.get("sequence", 0),
                rssi_dbm=rec.get("rssi_dbm", 0),
                channel=rec.get("channel", 0),
                source_mac=rec.get("source_mac", "?"),
                fw_drops=rec.get("firmware_drops_since_previous_frame", 0),
                sender_ip=rec.get("sender_ip", ""),
                spectrum=spectrum,
            )
            sink.write(recorder, row)
            frames += 1
            if frames % progress_every == 0:
                print(f"... {frames} frames", file=sys.stderr)
    return frames, malformed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("-p", "--port", type=int, default=5005)
    parser.add_argument("--chip", type=int, default=1)
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument("-n", "--count", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=0)
    parser.add_argument("--replay", type=Path, default=None,
                        help="convert a JSONL capture instead of UDP")
    parser.add_argument("--per-subcarrier", action="store_true",
                        help="append one scNN column per subcarrier")
    args = parser.parse_args(argv)

    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.count < 0:
        parser.error("--count must be 0 or greater")
    if args.timeout < 0:
        parser.error("--timeout must be 0 or greater")

    output_path = args.output or default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    recorder = AmpRecorder(per_subcarrier=args.per_subcarrier)
    sink = CsvSink(output_path)

    frames, malformed = 0, 0
    try:
        if args.replay:
            print(f"Converting {args.replay} -> {output_path}")
            frames, malformed = run_replay(recorder, sink, args.replay)
        else:
            print(f"Logging CSI amplitude to {output_path} "
                  f"(UDP {args.listen}:{args.port})")
            print("Press Ctrl-C to stop.")
            frames, malformed = run_udp(recorder, sink, args.listen,
                                        args.port, args.chip,
                                        args.count, args.timeout)
    except KeyboardInterrupt:
        print("\nStopped by user.")
        frames = sink.rows  # partial file is kept
    except OSError as error:
        print(f"CSI time log failed: {error}", file=sys.stderr)
        return 2
    finally:
        sink.close()

    print(f"Logged {frames} frames to {output_path}; "
          f"ignored {malformed} malformed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
