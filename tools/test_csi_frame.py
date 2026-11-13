import unittest

from csi_frame import (
    STATUS_OFFLINE,
    STATUS_ONLINE,
    BoundedPublisher,
    CsiFrameValue,
    FakePublisher,
    amplitude_spectrum,
    check_device_id,
    dedup_key,
    decode_frame,
    encode_frame,
    frame_topic,
    status_payload,
    status_topic,
)


def make_value(**overrides):
    fields = {
        "chip": 1,
        "sequence": 7,
        "rssi": -39,
        "channel": 10,
        "first_word_invalid": True,
        "mac": bytes([0x30, 0x16, 0x9D, 0x5F, 0xED, 0xD0]),
        "drops": 3,
        "payload": b"\x01\xff\x7f\x00" * 32,
    }
    fields.update(overrides)
    return CsiFrameValue(**fields)


class CodecTests(unittest.TestCase):
    def test_round_trip_preserves_metadata_and_payload(self):
        value = make_value()
        decoded = decode_frame(encode_frame(value))
        self.assertEqual(decoded, value)
        self.assertTrue(decoded.first_word_invalid)

    def test_rejects_bad_magic_version_chip(self):
        good = bytearray(encode_frame(make_value()))
        bad_magic = bytes(b"XXXX") + bytes(good[4:])
        with self.assertRaisesRegex(ValueError, "magic"):
            decode_frame(bad_magic)
        bad_version = bytes(good[:4]) + b"\x02" + bytes(good[5:])
        with self.assertRaisesRegex(ValueError, "version"):
            decode_frame(bad_version)
        with self.assertRaisesRegex(ValueError, "chip ID"):
            decode_frame(encode_frame(make_value(chip=2)))

    def test_rejects_length_mismatch_and_short_datagram(self):
        with self.assertRaisesRegex(ValueError, "length does not match"):
            decode_frame(encode_frame(make_value()) + b"\x00")
        with self.assertRaisesRegex(ValueError, "shorter than"):
            decode_frame(b"CSI1")

    def test_first_word_invalid_round_trips_both_ways(self):
        for flag in (False, True):
            decoded = decode_frame(encode_frame(make_value(
                first_word_invalid=flag)))
            self.assertEqual(decoded.first_word_invalid, flag)

    def test_amplitude_skips_invalid_first_word(self):
        payload = bytes([0, 100] + [0, 1] * 4)  # first pair would spike
        full = amplitude_spectrum(payload, False)
        skipped = amplitude_spectrum(payload, True)
        self.assertEqual(len(full), len(skipped) + 2)
        self.assertAlmostEqual(full[0], 100.0)
        self.assertTrue(all(a < 2.0 for a in skipped))


class TopicTests(unittest.TestCase):
    def test_frame_and_status_topics(self):
        self.assertEqual(frame_topic("esp32-csi-01"),
                         "csi/v1/esp32-csi-01/frame")
        self.assertEqual(status_topic("esp32-csi-01"),
                         "csi/v1/esp32-csi-01/status")

    def test_rejects_bad_device_ids(self):
        for bad in ("", "a/b", "x" * 33, None):
            with self.assertRaises(ValueError):
                check_device_id(bad)

    def test_status_payload_is_json_for_status_only(self):
        import json

        record = json.loads(status_payload("esp32-csi-01", STATUS_ONLINE))
        self.assertEqual(record["state"], STATUS_ONLINE)
        self.assertEqual(record["device_id"], "esp32-csi-01")
        with self.assertRaises(ValueError):
            status_payload("esp32-csi-01", "bogus")

    def test_dedup_key_separates_devices(self):
        self.assertNotEqual(dedup_key("a", 1), dedup_key("b", 1))
        self.assertEqual(dedup_key("a", 1), dedup_key("a", 1))


class PublisherSeamTests(unittest.TestCase):
    def test_fake_accepts_frames(self):
        fake = FakePublisher()
        self.assertTrue(fake.ready())
        self.assertTrue(fake.publish(make_value()))
        self.assertEqual(len(fake.published), 1)
        self.assertEqual(fake.transport_drops, 0)

    def test_unavailable_publisher_counts_drops(self):
        fake = FakePublisher(mode="down")
        self.assertFalse(fake.ready())
        self.assertFalse(fake.publish(make_value()))
        self.assertEqual(fake.transport_drops, 1)
        self.assertEqual(len(fake.published), 0)

    def test_flaky_publisher_recovers(self):
        fake = FakePublisher(mode="flaky", fail_times=2)
        self.assertFalse(fake.publish(make_value()))
        self.assertFalse(fake.publish(make_value()))
        self.assertTrue(fake.publish(make_value()))
        self.assertEqual(fake.transport_drops, 2)
        self.assertEqual(len(fake.published), 1)

    def test_bounded_publisher_prefers_freshness(self):
        inner = FakePublisher(mode="down")
        bounded = BoundedPublisher(inner, capacity=2)
        frames = [make_value(sequence=n) for n in range(4)]
        for frame in frames:
            bounded.offer(frame)
        # Oldest two discarded, newest two kept.
        self.assertEqual(bounded.transport_drops, 2)
        self.assertEqual([f.sequence for f in bounded.pending], [2, 3])

    def test_bounded_publisher_drains_in_order(self):
        inner = FakePublisher(mode="down")
        bounded = BoundedPublisher(inner, capacity=4)
        bounded.offer(make_value(sequence=1))
        bounded.offer(make_value(sequence=2))
        inner.mode = "ok"
        remaining = bounded.service()
        self.assertEqual(remaining, 0)
        self.assertEqual([f.sequence for f in inner.published], [1, 2])

    def test_drain_stops_on_failure(self):
        inner = FakePublisher(mode="flaky", fail_times=1)
        bounded = BoundedPublisher(inner, capacity=4)
        bounded.offer(make_value(sequence=1))
        bounded.offer(make_value(sequence=2))
        remaining = bounded.service()
        self.assertEqual(remaining, 2)
        self.assertEqual(bounded.transport_drops, 1)


if __name__ == "__main__":
    unittest.main()
