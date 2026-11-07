#!/usr/bin/env python3
"""Receive CSI1 UDP datagrams from an ESP32 and save them as JSON Lines."""

import argparse
import json
import socket
import sys
import time
from pathlib import Path

from csi_read import (
    CsiFrame,
    HEADER,
    MAGIC,
    MAX_LEN,
    default_output_path,
    frame_to_record,
)


def parse_frame_datagram(datagram, expected_chip=1):
    """Validate one complete CSI1 frame carried in a single UDP datagram."""
    if len(datagram) < HEADER.size:
        raise ValueError("datagram is shorter than the CSI1 header")

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
    ) = HEADER.unpack_from(datagram)

    if magic != MAGIC:
        raise ValueError("invalid CSI1 magic")
    if version != 1:
        raise ValueError(f"unsupported CSI1 version: {version}")
    if chip != expected_chip:
        raise ValueError(f"unexpected chip ID: {chip}")
    if length > MAX_LEN:
        raise ValueError(f"payload exceeds {MAX_LEN} bytes: {length}")
    if len(datagram) != HEADER.size + length:
        raise ValueError("datagram length does not match CSI1 header")

    return CsiFrame(
        version=version,
        chip=chip,
        sequence=sequence,
        rssi=rssi,
        channel=channel,
        first_word_invalid=bool(first_word_invalid),
        mac=mac,
        drops=drops,
        payload=datagram[HEADER.size:],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--listen",
        default="0.0.0.0",
        help="local address to bind (default: all network interfaces)",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=5005,
        help="UDP port to listen on (default: 5005)",
    )
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
        help="stop after N valid frames; 0 captures until Ctrl-C",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0,
        help="stop after N seconds; 0 has no time limit",
    )
    parser.add_argument(
        "--chip", type=int, default=1, help="expected CSI1 chip ID (default: 1)"
    )
    args = parser.parse_args()

    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.count < 0:
        parser.error("--count must be 0 or greater")
    if args.timeout < 0:
        parser.error("--timeout must be 0 or greater")

    output_path = args.output or default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame_count = 0
    malformed_count = 0
    started_at = time.monotonic()
    deadline = started_at + args.timeout if args.timeout else None

    print(f"Listening for CSI1 UDP frames on {args.listen}:{args.port}")
    print(f"Saving CSI frames to {output_path}")
    print("Press Ctrl-C to stop.")

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind((args.listen, args.port))
            receiver.settimeout(0.25)
            with output_path.open("a", encoding="utf-8") as output:
                while args.count == 0 or frame_count < args.count:
                    if deadline is not None and time.monotonic() >= deadline:
                        break

                    try:
                        datagram, address = receiver.recvfrom(
                            HEADER.size + MAX_LEN + 1
                        )
                    except socket.timeout:
                        continue

                    try:
                        frame = parse_frame_datagram(datagram, args.chip)
                    except ValueError as error:
                        malformed_count += 1
                        print(
                            f"Ignored malformed datagram from {address[0]}: {error}",
                            file=sys.stderr,
                        )
                        continue

                    record = frame_to_record(frame)
                    record["sender_ip"] = address[0]
                    record["sender_port"] = address[1]
                    output.write(json.dumps(record) + "\n")
                    output.flush()
                    frame_count += 1
    except KeyboardInterrupt:
        print("\nCapture stopped by user.")
    except OSError as error:
        print(f"CSI UDP receiver failed: {error}", file=sys.stderr)
        return 2

    elapsed = time.monotonic() - started_at
    print(
        f"Captured {frame_count} frames in {elapsed:.1f}s; "
        f"ignored {malformed_count} malformed datagrams; saved to {output_path}"
    )
    if frame_count == 0:
        print(
            "No CSI frames received. Check the ESP32 host IP/UDP port, "
            "Wi-Fi network, and host firewall.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
