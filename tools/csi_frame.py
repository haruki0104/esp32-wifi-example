#!/usr/bin/env python3
"""Host mirror of the Phase 1 CSI frame codec and publisher seam.

Mirrors src/csi/CsiFrame.h wire bytes and src/csi/FramePublisher.h policy so
the protocol can be tested on the host before an MQTT library is integrated
into the firmware. Raw payloads are preserved; I/Q layout is not decoded here
except for the documented amplitude helper used by the visualizer plan.
"""

import json
import math
import re
from collections import deque
from dataclasses import dataclass, field

from csi_read import HEADER, MAGIC, MAX_LEN

FRAME_BASE_TOPIC = "csi/v1"
STATUS_ONLINE = "online"
STATUS_OFFLINE = "offline"
_DEVICE_RE = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")


def check_device_id(device_id):
    """Validate the stable device identifier used in topics."""
    if not isinstance(device_id, str) or not _DEVICE_RE.match(device_id):
        raise ValueError(f"invalid device ID: {device_id!r}")
    return device_id


def frame_topic(device_id):
    """Binary frame topic. Publish with retain=False, QoS 0 (Phase 0)."""
    return f"{FRAME_BASE_TOPIC}/{check_device_id(device_id)}/frame"


def status_topic(device_id):
    """Low-rate JSON status topic carrying online/offline state."""
    return f"{FRAME_BASE_TOPIC}/{check_device_id(device_id)}/status"


def status_payload(device_id, state, transport_drops=0, firmware_drops=0):
    """Encode the low-rate status message (JSON is for status, not samples)."""
    if state not in (STATUS_ONLINE, STATUS_OFFLINE):
        raise ValueError(f"invalid status state: {state!r}")
    return json.dumps(
        {
            "device_id": check_device_id(device_id),
            "state": state,
            "transport_drops": transport_drops,
            "firmware_drops": firmware_drops,
        }
    )


def dedup_key(device_id, sequence):
    """Subscriber deduplication key: device ID plus frame sequence."""
    return (check_device_id(device_id), int(sequence))


@dataclass
class CsiFrameValue:
    """Transport-agnostic frame value mirroring the firmware CsiFrame."""

    chip: int = 1
    sequence: int = 0
    rssi: int = 0
    channel: int = 0
    first_word_invalid: bool = False
    mac: bytes = b"\x00" * 6
    drops: int = 0
    payload: bytes = b""

    def __post_init__(self):
        if len(self.payload) > MAX_LEN:
            raise ValueError(f"payload exceeds {MAX_LEN} bytes")
        if len(self.mac) != 6:
            raise ValueError("mac must be 6 bytes")


def encode_frame(value):
    """Encode a frame value to CSI1 wire bytes (header + raw payload)."""
    header = HEADER.pack(
        MAGIC,
        1,  # version
        value.chip,
        len(value.payload),
        value.sequence & 0xFFFFFFFF,
        value.rssi,
        value.channel,
        1 if value.first_word_invalid else 0,
        0,  # reserved
        value.mac,
        value.drops & 0xFFFF,
    )
    return header + value.payload


def decode_frame(wire, expected_chip=1):
    """Strict-decode exactly one CSI1 frame from wire bytes."""
    from csi_udp_read import parse_frame_datagram

    parsed = parse_frame_datagram(wire, expected_chip)
    return CsiFrameValue(
        chip=parsed.chip,
        sequence=parsed.sequence,
        rssi=parsed.rssi,
        channel=parsed.channel,
        first_word_invalid=parsed.first_word_invalid,
        mac=parsed.mac,
        drops=parsed.drops,
        payload=parsed.payload,
    )


def amplitude_spectrum(payload, first_word_invalid):
    """Per-subcarrier amplitude; skips the first 4 bytes when flagged invalid.

    Payload order is imaginary byte then real byte per complex sample.
    """
    data = bytes(payload)
    start = 4 if first_word_invalid else 0
    usable = data[start:]
    if len(usable) % 2:
        raise ValueError("payload has an incomplete complex sample")
    spectrum = []
    for offset in range(0, len(usable), 2):
        imag = int.from_bytes(data[start + offset : start + offset + 1],
                              "big", signed=True)
        real = int.from_bytes(data[start + offset + 1 : start + offset + 2],
                              "big", signed=True)
        spectrum.append(math.sqrt(real * real + imag * imag))
    return spectrum


class FakePublisher:
    """Test double for the FramePublisher seam.

    Modes: "ok" accepts everything; "down" reports not ready; "flaky"
    fails the next `fail_times` publishes, then accepts.
    """

    def __init__(self, mode="ok", fail_times=0):
        self.mode = mode
        self.fail_times = fail_times
        self.published = []
        self.transport_drops = 0

    def ready(self):
        return self.mode != "down"

    def publish(self, frame):
        if not self.ready():
            self.transport_drops += 1
            return False
        if self.mode == "flaky" and self.fail_times > 0:
            self.fail_times -= 1
            self.transport_drops += 1
            return False
        self.published.append(frame)
        return True

    def service(self):
        pass


class BoundedPublisher:
    """Freshness-first bounded backlog around another publisher.

    When disconnected or overloaded, discards the oldest queued frame,
    increments the transport-drop counter, and keeps sequence numbers so the
    host can identify gaps. No unbounded queue, no offline archive.
    """

    def __init__(self, inner, capacity=8):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.inner = inner
        self.capacity = capacity
        self.pending = deque()
        self.transport_drops = 0

    def ready(self):
        return self.inner.ready()

    def offer(self, frame):
        """Enqueue a frame, dropping the oldest when full (counts a drop)."""
        if len(self.pending) >= self.capacity:
            self.pending.popleft()
            self.transport_drops += 1
        self.pending.append(frame)
        return True

    def service(self):
        """Drain the queue in order; stops on the first failure."""
        self.inner.service()
        while self.pending and self.inner.ready():
            frame = self.pending[0]
            if not self.inner.publish(frame):
                self.transport_drops += 1
                break
            self.pending.popleft()
        return len(self.pending)
