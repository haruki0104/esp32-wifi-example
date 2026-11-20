import csv
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from csi_aggregate import (
    CSV_COLUMNS,
    LinearClock,
    NodeStream,
    bucket_index,
    fit_clock,
    merged_rows,
    replay,
    seq_gap,
    write_csv,
)


def make_payload(amp=10, carriers=8):
    out = bytearray()
    for _ in range(carriers):
        out += bytes([0, amp])  # imaginary byte then real byte
    return bytes(out)


def make_record(node_id, sequence, host_time, amp=10, rx_ts_us=None,
                first_word_invalid=False):
    return {
        "node_id": node_id,
        "sequence": sequence,
        "captured_at_utc": None,
        "unix_time": host_time,
        "rx_ts_us": rx_ts_us,
        "rssi_dbm": -40,
        "first_word_invalid": first_word_invalid,
        "csi_payload_hex": make_payload(amp).hex(),
    }


def add(stream, sequence, host_time, amp=10, node_time_s=None):
    return stream.add(
        sequence=sequence,
        payload=make_payload(amp),
        first_word_invalid=False,
        rssi=-40,
        host_time=host_time,
        node_time_s=node_time_s,
    )


class ClockFitTests(unittest.TestCase):
    def test_recovers_slope_and_offset(self):
        slope, intercept = 1.0001, 100.0
        pairs = [(x / 10.0, slope * (x / 10.0) + intercept)
                 for x in range(200)]
        got_slope, got_intercept, rms_ms, used = fit_clock(pairs)

        self.assertAlmostEqual(got_slope, slope, places=9)
        self.assertAlmostEqual(got_intercept, intercept, places=6)
        self.assertLess(rms_ms, 1e-3)
        # The robust pass always trims the worst 10% once.
        self.assertLessEqual(used, len(pairs))
        self.assertGreaterEqual(used, int(len(pairs) * 0.85))

    def test_ignores_a_gross_outlier(self):
        slope, intercept = 1.0, 5.0
        pairs = [(x / 10.0, slope * (x / 10.0) + intercept)
                 for x in range(200)]
        pairs[57] = (pairs[57][0], pairs[57][1] + 3.0)  # 3 s hiccup
        got_slope, got_intercept, rms_ms, _ = fit_clock(pairs)

        self.assertAlmostEqual(got_slope, slope, places=6)
        self.assertAlmostEqual(got_intercept, intercept, places=3)
        self.assertLess(rms_ms, 5.0)

    def test_rejects_a_single_pair(self):
        with self.assertRaises(ValueError):
            fit_clock([(0.0, 1.0)])

    def test_identity_before_enough_pairs(self):
        stream = NodeStream(1, dt=0.1)
        add(stream, 1, host_time=10.0, node_time_s=3.0)
        # One pair cannot establish a fit; arrival time is the only safe
        # timeline because the node clock has a different epoch.
        self.assertAlmostEqual(stream.clock.map(3.0, fallback=10.0), 10.0)

    def test_maps_after_fit_and_falls_back_on_disagreement(self):
        clock = LinearClock(min_pairs=20)
        for index in range(20):
            clock.add(index / 10.0, 100.0 + index / 10.0)

        self.assertAlmostEqual(clock.map(5.0, fallback=105.0), 105.0, places=6)
        self.assertAlmostEqual(clock.map(900.0, fallback=105.0), 105.0)
        self.assertEqual(clock.fallbacks, 1)


class SequenceTests(unittest.TestCase):
    def test_gap_arithmetic(self):
        self.assertEqual(seq_gap(None, 5), 0)
        self.assertEqual(seq_gap(5, 6), 0)
        self.assertEqual(seq_gap(5, 8), 2)
        self.assertEqual(seq_gap(0xFFFFFFFF, 0), 0)
        self.assertEqual(seq_gap(10, 9), 0)  # backwards replay, not a gap

    def test_stream_counts_gaps_and_duplicates(self):
        stream = NodeStream(1, dt=0.1)
        for sequence in (1, 2, 3):
            self.assertTrue(add(stream, sequence, host_time=10.0 + sequence))
        self.assertEqual(stream.gaps, 0)

        self.assertFalse(add(stream, 2, host_time=11.0))
        self.assertEqual(stream.duplicates, 1)
        self.assertEqual(stream.frames, 3)

        add(stream, 6, host_time=14.0)
        self.assertEqual(stream.gaps, 2)


class BucketTests(unittest.TestCase):
    def test_bucket_index_is_floor_not_round(self):
        self.assertEqual(bucket_index(1.24, 0.1), 12)
        self.assertEqual(bucket_index(1.29, 0.1), 12)
        self.assertEqual(bucket_index(1.30, 0.1), 13)

    def test_frames_in_one_bucket_are_averaged(self):
        stream = NodeStream(1, dt=0.1)
        add(stream, 1, host_time=10.00, amp=10)
        add(stream, 2, host_time=10.02, amp=20)

        rows = [row for _, row in merged_rows([stream], 100, 101, 0.1)]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["frames"], 2)
        self.assertAlmostEqual(rows[0]["amp_mean"], 15.0)
        self.assertEqual(rows[0]["is_gap"], 0)

    def test_empty_bucket_is_an_explicit_gap(self):
        stream = NodeStream(1, dt=0.1)
        add(stream, 1, host_time=10.00, amp=10)
        add(stream, 2, host_time=10.30, amp=10)

        rows = [row for _, row in merged_rows([stream], 100, 104, 0.1)]
        self.assertEqual([row["is_gap"] for row in rows], [0, 1, 1, 0])
        self.assertIsNone(rows[1]["amp_mean"])

    def test_two_nodes_share_the_grid(self):
        first = NodeStream(1, dt=0.1)
        second = NodeStream(2, dt=0.1)
        add(first, 1, host_time=10.0)
        add(second, 1, host_time=10.0)
        add(second, 2, host_time=10.2)

        rows = [row for _, row in merged_rows([first, second], 100, 103, 0.1)]
        self.assertEqual([(r["node_id"], r["is_gap"]) for r in rows],
                         [(1, 0), (2, 0), (1, 1), (2, 1), (1, 1), (2, 0)])


class ReplayTests(unittest.TestCase):
    def write_jsonl(self, path, node_id, frames=30, skip=(), start=10.0):
        with Path(path).open("w", encoding="utf-8") as handle:
            for index in range(frames):
                if index in skip:
                    continue
                record = make_record(node_id, index, start + index * 0.05,
                                     rx_ts_us=int((start + index * 0.05) * 1e6))
                handle.write(json.dumps(record) + "\n")

    def test_replay_two_nodes_writes_long_csv(self):
        with TemporaryDirectory() as tmp:
            one = Path(tmp) / "n1.jsonl"
            two = Path(tmp) / "n2.jsonl"
            self.write_jsonl(one, 1)
            # Two consecutive frames: 0.1 s buckets hold two frames each, so
            # one missing frame would not leave a hole.
            self.write_jsonl(two, 2, skip=(14, 15))

            streams = replay([one, two], dt=0.1)
            self.assertEqual(sorted(streams), [1, 2])
            self.assertEqual(streams[2].gaps, 2)

            indices = [i for s in streams.values() for i in s.buckets]
            rows = [row for _, row in merged_rows(
                list(streams.values()), min(indices), max(indices) + 1, 0.1)]
            out = Path(tmp) / "merged.csv"
            write_csv(out, rows)

            with out.open(newline="", encoding="utf-8") as handle:
                parsed = list(csv.DictReader(handle))
            self.assertEqual(list(parsed[0]), CSV_COLUMNS)
            self.assertEqual({row["node_id"] for row in parsed}, {"1", "2"})
            self.assertIn("1", {row["is_gap"] for row in parsed})
            self.assertIn("0", {row["is_gap"] for row in parsed})

    def test_replay_rejects_v1_records(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "v1.jsonl"
            record = make_record(1, 0, 10.0)
            record.pop("node_id")
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            self.assertEqual(replay([path], dt=0.1), {})


if __name__ == "__main__":
    unittest.main()
