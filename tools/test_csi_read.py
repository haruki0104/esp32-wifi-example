import struct
import unittest

from csi_read import (
    CsiFrameParser,
    FLAG_BANDWIDTH_40MHZ,
    FLAG_FIRST_WORD_INVALID,
    FLAG_ROLE_TX,
    HEADER,
    HEADER_V2,
    KNOWN_CHIPS,
    MAGIC,
    MAX_FRAME_SIZE,
    frame_to_record,
    parse_frame,
)
from csi_udp_read import parse_frame_datagram


def make_frame(
    payload=b"\x01\xff\x7f",
    sequence=7,
    chip=1,
    rssi=-42,
    channel=6,
    invalid=0,
    drops=2,
):
    header = HEADER.pack(
        0x31495343,
        1,
        chip,
        len(payload),
        sequence,
        rssi,
        channel,
        invalid,
        0,
        bytes.fromhex("aabbccddeeff"),
        drops,
    )
    return header + payload


class CsiFrameParserTests(unittest.TestCase):
    def test_extracts_frame_after_text_and_fragmented_input(self):
        parser = CsiFrameParser()
        wire = b"ESP32 boot\r\nCSI_STREAM_START\r\n" + make_frame()

        self.assertEqual(parser.feed(wire[:17]), [])
        self.assertEqual(parser.feed(wire[17:36]), [])
        frames = parser.feed(wire[36:])

        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertEqual(frame.sequence, 7)
        self.assertEqual(frame.rssi, -42)
        self.assertEqual(frame.channel, 6)
        self.assertEqual(frame.payload, b"\x01\xff\x7f")
        self.assertIn(b"CSI_STREAM_START", parser.take_text())

    def test_ignores_wrong_chip_and_resynchronizes(self):
        parser = CsiFrameParser(expected_chip=1)
        frames = parser.feed(make_frame(chip=2) + make_frame(sequence=8))

        self.assertEqual([frame.sequence for frame in frames], [8])

    def test_rejects_oversized_length_and_finds_next_frame(self):
        oversized_header = HEADER.pack(
            0x31495343,
            1,
            1,
            513,
            11,
            -50,
            1,
            0,
            0,
            bytes(6),
            0,
        )
        parser = CsiFrameParser()

        frames = parser.feed(oversized_header + make_frame(sequence=12))

        self.assertEqual([frame.sequence for frame in frames], [12])

    def test_record_preserves_raw_payload_and_metadata(self):
        parser = CsiFrameParser()
        frame = parser.feed(make_frame(invalid=1))[0]

        record = frame_to_record(frame)

        self.assertEqual(record["csi_payload_hex"], "01ff7f")
        self.assertEqual(record["rssi_dbm"], -42)
        self.assertEqual(record["source_mac"], "aa:bb:cc:dd:ee:ff")
        self.assertTrue(record["first_word_invalid"])
        self.assertIn("captured_at_utc", record)

    def test_udp_datagram_contains_one_complete_frame(self):
        frame = parse_frame_datagram(make_frame(invalid=1))

        self.assertEqual(frame.sequence, 7)
        self.assertEqual(frame.payload, b"\x01\xff\x7f")
        self.assertTrue(frame.first_word_invalid)

    def test_udp_parser_rejects_trailing_or_missing_payload(self):
        with self.assertRaisesRegex(ValueError, "length does not match"):
            parse_frame_datagram(make_frame() + b"\x00")

        with self.assertRaisesRegex(ValueError, "shorter than"):
            parse_frame_datagram(b"CSI1")

    def test_udp_parser_rejects_unexpected_chip(self):
        with self.assertRaisesRegex(ValueError, "unexpected chip ID"):
            parse_frame_datagram(make_frame(chip=2))


def make_frame_v2(
    payload=b"\x01\xff\x7f",
    sequence=9,
    node_id=2,
    rx_ts_us=1_700_000_000_123_456,
    first_word_invalid=True,
    bandwidth_40=False,
    role_tx=False,
    drops=4,
):
    flags = 0
    if first_word_invalid:
        flags |= FLAG_FIRST_WORD_INVALID
    if bandwidth_40:
        flags |= FLAG_BANDWIDTH_40MHZ
    if role_tx:
        flags |= FLAG_ROLE_TX
    header = HEADER_V2.pack(
        MAGIC,
        2,
        1,
        len(payload),
        sequence,
        rx_ts_us,
        node_id,
        flags,
        -38,
        10,
        drops,
        bytes.fromhex("30169d5fedd0"),
    )
    return header + payload


class MultiNodeFrameTests(unittest.TestCase):
    def test_v2_frame_round_trips_node_identity_and_time(self):
        frame = parse_frame(make_frame_v2())

        self.assertEqual(frame.version, 2)
        self.assertEqual(frame.node_id, 2)
        self.assertEqual(frame.rx_ts_us, 1_700_000_000_123_456)
        self.assertTrue(frame.first_word_invalid)
        self.assertEqual(frame.bandwidth_mhz, 20)
        self.assertEqual(frame.role, "rx")
        self.assertEqual(frame.drops, 4)

    def test_v2_flags_decode_bandwidth_and_role(self):
        frame = parse_frame(make_frame_v2(bandwidth_40=True, role_tx=True))

        self.assertEqual(frame.bandwidth_mhz, 40)
        self.assertEqual(frame.role, "tx")

    def test_v1_frames_still_parse_with_unset_v2_fields(self):
        frame = parse_frame(make_frame())

        self.assertEqual(frame.version, 1)
        self.assertEqual(frame.node_id, 0)
        self.assertIsNone(frame.rx_ts_us)
        self.assertEqual(frame.bandwidth_mhz, 20)
        self.assertEqual(frame.role, "rx")

    def test_v2_rejects_unset_node_id(self):
        with self.assertRaisesRegex(ValueError, "node_id"):
            parse_frame(make_frame_v2(node_id=0))

    def test_stream_parser_over_v2_frames(self):
        parser = CsiFrameParser()
        wire = make_frame_v2(sequence=1) + make_frame_v2(sequence=2)

        frames = parser.feed(wire)

        self.assertEqual([frame.sequence for frame in frames], [1, 2])
        self.assertTrue(all(frame.node_id == 2 for frame in frames))

    def test_mixed_v1_and_v2_stream(self):
        parser = CsiFrameParser()
        wire = make_frame(sequence=1) + make_frame_v2(sequence=2)

        frames = parser.feed(wire)

        self.assertEqual([frame.version for frame in frames], [1, 2])
        self.assertEqual([frame.sequence for frame in frames], [1, 2])

    def test_max_payload_v2_fits_the_receive_buffer(self):
        wire = make_frame_v2(payload=b"\x00" * 512)

        self.assertEqual(len(wire), MAX_FRAME_SIZE)
        self.assertEqual(parse_frame(wire).payload, b"\x00" * 512)

    def test_chip_sets_accept_s3_and_reject_unknown(self):
        s3 = bytearray(make_frame_v2())
        s3[5] = 2  # chip byte

        self.assertEqual(parse_frame(bytes(s3), KNOWN_CHIPS).chip, 2)
        with self.assertRaisesRegex(ValueError, "unexpected chip ID"):
            parse_frame(bytes(s3), 1)  # single-id callers stay strict

        c5 = bytearray(make_frame_v2())
        c5[5] = 5
        with self.assertRaisesRegex(ValueError, "unexpected chip ID"):
            parse_frame(bytes(c5), KNOWN_CHIPS)

    def test_record_carries_v2_fields(self):
        record = frame_to_record(parse_frame(make_frame_v2()))

        self.assertEqual(record["frame_version"], 2)
        self.assertEqual(record["node_id"], 2)
        self.assertEqual(record["rx_ts_us"], 1_700_000_000_123_456)
        self.assertEqual(record["bandwidth_mhz"], 20)
        self.assertEqual(record["node_role"], "rx")


if __name__ == "__main__":
    unittest.main()
