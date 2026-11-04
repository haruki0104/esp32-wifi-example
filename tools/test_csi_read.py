import struct
import unittest

from csi_read import CsiFrameParser, HEADER, frame_to_record


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


if __name__ == "__main__":
    unittest.main()
