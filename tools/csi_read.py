#!/usr/bin/env python3
"""Capture CSI1 frames from the ESP32 serial stream into JSON Lines."""

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import struct

MAGIC_BYTES = b"CSI1"
MAGIC = 0x31495343
HEADER = struct.Struct("<IBBHIbBBB6sH")
MAX_LEN = 512
MAX_TEXT_BUFFER = 65536


@dataclass(frozen=True)
class CsiFrame:
    version: int
    chip: int
    sequence: int
    rssi: int
    channel: int
    first_word_invalid: bool
    mac: bytes
    drops: int
    payload: bytes


class CsiFrameParser:
    """Incrementally extracts framed CSI records from mixed serial output."""

    def __init__(self, expected_chip=1):
        self.expected_chip = expected_chip
        self.pending = bytearray()
        self.text = bytearray()

    def _save_text(self, data):
        if not data:
            return
        self.text.extend(data)
        if len(self.text) > MAX_TEXT_BUFFER:
            del self.text[:-MAX_TEXT_BUFFER]

    def take_text(self):
        data = bytes(self.text)
        self.text.clear()
        return data

    def feed(self, data):
        self.pending.extend(data)
        frames = []

        while True:
            start = self.pending.find(MAGIC_BYTES)
            if start < 0:
                if len(self.pending) > len(MAGIC_BYTES) - 1:
                    keep = len(MAGIC_BYTES) - 1
                    self._save_text(self.pending[:-keep])
                    del self.pending[:-keep]
                break

            if start:
                self._save_text(self.pending[:start])
                del self.pending[:start]

            if len(self.pending) < HEADER.size:
                break

            (
                magic,
                version,
                chip,
                length,
                sequence,
                rssi,
                channel,
                first_word_invalid,
                _reserved,
                mac,
                drops,
            ) = HEADER.unpack_from(self.pending)

            if (
                magic != MAGIC
                or version != 1
                or chip != self.expected_chip
                or length > MAX_LEN
            ):
                self._save_text(self.pending[:1])
                del self.pending[:1]
                continue

            frame_size = HEADER.size + length
            if len(self.pending) < frame_size:
                break

            payload = bytes(self.pending[HEADER.size:frame_size])
            del self.pending[:frame_size]
            frames.append(
                CsiFrame(
                    version=version,
                    chip=chip,
                    sequence=sequence,
                    rssi=rssi,
                    channel=channel,
                    first_word_invalid=bool(first_word_invalid),
                    mac=mac,
                    drops=drops,
                    payload=payload,
                )
            )

        return frames


def frame_to_record(frame):
    """Preserve raw bytes; do not assume chip-specific CSI sample layout."""
    return {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "sequence": frame.sequence,
        "chip": frame.chip,
        "rssi_dbm": frame.rssi,
        "channel": frame.channel,
        "first_word_invalid": frame.first_word_invalid,
        "source_mac": ":".join(f"{byte:02x}" for byte in frame.mac),
        "firmware_drops_since_previous_frame": frame.drops,
        "payload_length": len(frame.payload),
        "csi_payload_hex": frame.payload.hex(),
    }


def default_output_path():
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("captures") / f"csi-{timestamp}.jsonl"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-p", "--port", required=True, help="ESP32 serial port")
    parser.add_argument("-b", "--baud", type=int, default=115200)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="JSONL output path (default: captures/csi-<UTC timestamp>.jsonl)",
    )
    parser.add_argument(
        "-n",
        "--count",
        type=int,
        default=0,
        help="stop after N frames; 0 captures until Ctrl-C (default: 0)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0,
        help="stop after N seconds; 0 has no time limit (default: 0)",
    )
    parser.add_argument("--chip", type=int, default=1, help="CSI1 chip ID (default: 1)")
    args = parser.parse_args()

    if args.count < 0:
        parser.error("--count must be 0 or greater")
    if args.timeout < 0:
        parser.error("--timeout must be 0 or greater")
    if args.baud <= 0:
        parser.error("--baud must be greater than 0")

    try:
        import serial
    except ImportError:
        sys.stderr.write(
            "pyserial is required: python3 -m pip install -r "
            "tools/requirements-csi.txt\n"
        )
        return 2

    output_path = args.output or default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame_parser = CsiFrameParser(expected_chip=args.chip)
    frame_count = 0
    started_at = time.monotonic()
    deadline = started_at + args.timeout if args.timeout else None

    print(f"Listening on {args.port} at {args.baud} baud")
    print(f"Saving CSI frames to {output_path}")
    print("Press Ctrl-C to stop.")

    try:
        with serial.Serial(args.port, args.baud, timeout=0.25) as port:
            with output_path.open("a", encoding="utf-8") as output:
                while args.count == 0 or frame_count < args.count:
                    if deadline is not None and time.monotonic() >= deadline:
                        break

                    for frame in frame_parser.feed(port.read(4096)):
                        output.write(json.dumps(frame_to_record(frame)) + "\n")
                        output.flush()
                        frame_count += 1
                        if args.count and frame_count >= args.count:
                            break
    except KeyboardInterrupt:
        print("\nCapture stopped by user.")

    serial_text = frame_parser.take_text().decode("utf-8", errors="replace")
    for line in serial_text.splitlines():
        if line.strip():
            print(f"serial: {line}")

    elapsed = time.monotonic() - started_at
    print(f"Captured {frame_count} frames in {elapsed:.1f}s; saved to {output_path}")
    if frame_count == 0:
        print(
            "No CSI frames received. Check that the ESP32 reports "
            "CSI_STREAM_START and that the port/baud/chip match.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
