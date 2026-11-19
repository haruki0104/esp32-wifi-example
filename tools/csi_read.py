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
# Version 1: the single-ESP32 frame that archived captures use.
HEADER_V1 = struct.Struct("<IBBHIbBBB6sH")  # 24 bytes
# Version 2: multi-node frame; layout frozen in docs/csi-multi-node-design.md.
HEADER_V2 = struct.Struct("<IBBHIQBBbBH6s")  # 32 bytes
HEADER = HEADER_V1  # legacy name for the v1 header
MAX_LEN = 512
MAX_HEADER_SIZE = max(HEADER_V1.size, HEADER_V2.size)
MAX_FRAME_SIZE = MAX_HEADER_SIZE + MAX_LEN
MAX_TEXT_BUFFER = 65536

NODE_ID_UNSET = 0
NODE_ID_TX = 255

FLAG_FIRST_WORD_INVALID = 0x01
FLAG_BANDWIDTH_MASK = 0x06
FLAG_BANDWIDTH_40MHZ = 0x02
FLAG_ROLE_TX = 0x08


@dataclass(frozen=True)
class HeaderFields:
    """One decoded CSI1 header, with the version-specific bits normalized."""

    version: int
    chip: int
    length: int
    sequence: int
    rx_ts_us: int | None
    node_id: int
    flags: int
    rssi: int
    channel: int
    drops: int
    mac: bytes
    size: int

    @property
    def first_word_invalid(self):
        # v1 carried the flag as its own byte; v2 packs it into bit 0.
        if self.version == 1:
            return bool(self.flags)
        return bool(self.flags & FLAG_FIRST_WORD_INVALID)

    @property
    def bandwidth_mhz(self):
        if self.version == 1:
            return 20
        return 40 if (self.flags & FLAG_BANDWIDTH_MASK) == FLAG_BANDWIDTH_40MHZ else 20

    @property
    def role(self):
        if self.version == 1:
            return "rx"
        return "tx" if self.flags & FLAG_ROLE_TX else "rx"


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
    node_id: int = NODE_ID_UNSET
    rx_ts_us: int | None = None
    bandwidth_mhz: int = 20
    role: str = "rx"


def header_size(data):
    """Header length implied by the version byte, or None if undecidable."""
    if len(data) < 6:
        return None
    version = data[4]
    if version == 1:
        return HEADER_V1.size
    if version == 2:
        return HEADER_V2.size
    raise ValueError(f"unsupported CSI1 version: {version}")


def unpack_header(data, expected_chip=1, size=None):
    """Decode one header; raises ValueError when it is not a valid header.

    A streaming caller can pass the header length it already derived from
    `header_size`, so the version byte is not inspected twice.
    """
    if data[:4] != MAGIC_BYTES:
        raise ValueError("invalid CSI1 magic")
    if size is None:
        size = header_size(data)
        if size is None:
            raise ValueError("datagram is shorter than the CSI1 header")
    if len(data) < size:
        raise ValueError("datagram is shorter than the CSI1 header")

    version = data[4]
    if version == 1:
        (
            _magic,
            _version,
            chip,
            length,
            sequence,
            rssi,
            channel,
            first_word_invalid,
            _reserved,
            mac,
            drops,
        ) = HEADER_V1.unpack_from(data)
        if chip != expected_chip:
            raise ValueError(f"unexpected chip ID: {chip}")
        if length > MAX_LEN:
            raise ValueError(f"payload exceeds {MAX_LEN} bytes: {length}")
        return HeaderFields(
            version=1, chip=chip, length=length, sequence=sequence,
            rx_ts_us=None, node_id=NODE_ID_UNSET, flags=first_word_invalid,
            rssi=rssi, channel=channel, drops=drops, mac=mac,
            size=HEADER_V1.size,
        )

    if version == 2:
        (
            _magic,
            _version,
            chip,
            length,
            sequence,
            rx_ts_us,
            node_id,
            flags,
            rssi,
            channel,
            drops,
            mac,
        ) = HEADER_V2.unpack_from(data)
        if chip != expected_chip:
            raise ValueError(f"unexpected chip ID: {chip}")
        if length > MAX_LEN:
            raise ValueError(f"payload exceeds {MAX_LEN} bytes: {length}")
        if node_id == NODE_ID_UNSET:
            raise ValueError("CSI1 v2 frame has node_id 0 (CSI_NODE_ID unset)")
        return HeaderFields(
            version=2, chip=chip, length=length, sequence=sequence,
            rx_ts_us=rx_ts_us, node_id=node_id, flags=flags, rssi=rssi,
            channel=channel, drops=drops, mac=mac, size=HEADER_V2.size,
        )

    raise ValueError(f"unsupported CSI1 version: {version}")


def parse_frame(data, expected_chip=1):
    """Strictly decode exactly one complete frame (header + payload)."""
    head = unpack_header(data, expected_chip)
    if len(data) != head.size + head.length:
        raise ValueError("datagram length does not match the CSI1 header")
    payload = bytes(data[head.size:head.size + head.length])
    return CsiFrame(
        version=head.version,
        chip=head.chip,
        sequence=head.sequence,
        rssi=head.rssi,
        channel=head.channel,
        first_word_invalid=head.first_word_invalid,
        mac=head.mac,
        drops=head.drops,
        payload=payload,
        node_id=head.node_id,
        rx_ts_us=head.rx_ts_us,
        bandwidth_mhz=head.bandwidth_mhz,
        role=head.role,
    )


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

            if len(self.pending) < 6:
                break
            try:
                size = header_size(self.pending)
            except ValueError:
                self._save_text(self.pending[:1])
                del self.pending[:1]
                continue
            if size is None:
                break
            if len(self.pending) < size:
                break

            try:
                head = unpack_header(self.pending, self.expected_chip,
                                     size=size)
            except ValueError:
                self._save_text(self.pending[:1])
                del self.pending[:1]
                continue

            frame_size = head.size + head.length
            if len(self.pending) < frame_size:
                break

            frames.append(parse_frame(self.pending[:frame_size],
                                      self.expected_chip))
            del self.pending[:frame_size]

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
        # v2 fields; v1 records carry the unset/None defaults.
        "frame_version": frame.version,
        "node_id": frame.node_id,
        "rx_ts_us": frame.rx_ts_us,
        "bandwidth_mhz": frame.bandwidth_mhz,
        "node_role": frame.role,
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
            "CSI_UDP_START and that the UDP host IP/port and chip match.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
