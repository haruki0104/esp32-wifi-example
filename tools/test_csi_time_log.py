import csv
import io
import unittest

from csi_time_log import AmpRecorder, BASE_COLUMNS, spectrum_stats


def make_row(recorder, seq, spectrum=None, **overrides):
    fields = {
        "unix_time": 1700000000.0 + seq,
        "captured_at_utc": "2026-01-01T00:00:00+00:00",
        "sequence": seq,
        "rssi_dbm": -45,
        "channel": 10,
        "source_mac": "30:16:9d:5f:ed:d0",
        "fw_drops": 0,
        "sender_ip": "192.168.212.127",
        "spectrum": [10.0, 20.0, 30.0, 40.0] if spectrum is None else spectrum,
    }
    fields.update(overrides)
    return recorder.make_row(**fields)


class SpectrumStatsTests(unittest.TestCase):
    def test_summarizes_values(self):
        stats = spectrum_stats([10.0, 20.0, 30.0, 40.0])
        self.assertEqual(stats["n"], 4)
        self.assertAlmostEqual(stats["mean"], 25.0)
        self.assertAlmostEqual(stats["min"], 10.0)
        self.assertAlmostEqual(stats["max"], 40.0)
        self.assertGreater(stats["std"], 0)

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            spectrum_stats([])


class AmpRecorderTests(unittest.TestCase):
    def test_first_frame_has_no_gap(self):
        recorder = AmpRecorder()
        row = make_row(recorder, 100)
        self.assertEqual(row["seq_gap"], 0)
        self.assertEqual(row["sequence"], 100)
        self.assertEqual(row["n_subcarriers"], 4)
        self.assertAlmostEqual(float(row["amp_mean"]), 25.0)
        for column in BASE_COLUMNS:
            self.assertIn(column, row)

    def test_continuous_sequence_has_no_gap(self):
        recorder = AmpRecorder()
        make_row(recorder, 100)
        row = make_row(recorder, 101)
        self.assertEqual(row["seq_gap"], 0)

    def test_jump_counts_missing_frames(self):
        recorder = AmpRecorder()
        make_row(recorder, 100)
        row = make_row(recorder, 105)
        self.assertEqual(row["seq_gap"], 4)

    def test_wraparound_has_no_gap(self):
        recorder = AmpRecorder()
        make_row(recorder, 0xFFFFFFFF)
        row = make_row(recorder, 0)
        self.assertEqual(row["seq_gap"], 0)

    def test_csv_round_trip(self):
        recorder = AmpRecorder()
        rows = [make_row(recorder, seq) for seq in (7, 8, 10)]
        self.assertEqual([r["seq_gap"] for r in rows], [0, 0, 1])
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=recorder.columns)
        writer.writeheader()
        writer.writerows(rows)
        buffer.seek(0)
        parsed = list(csv.DictReader(buffer))
        self.assertEqual(len(parsed), 3)
        self.assertEqual(parsed[2]["sequence"], "10")
        self.assertEqual(parsed[2]["seq_gap"], "1")

    def test_per_subcarrier_columns(self):
        recorder = AmpRecorder(per_subcarrier=True)
        row = make_row(recorder, 1, spectrum=[1.0, 2.0, 3.0])
        self.assertIn("sc00", row)
        self.assertIn("sc02", row)
        self.assertNotIn("sc03", row)
        self.assertEqual(row["sc01"], "2.000")
        # Width is fixed by the first frame; longer frames are truncated.
        row2 = make_row(recorder, 2, spectrum=[1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertNotIn("sc03", row2)
        self.assertEqual(row2["n_subcarriers"], 5)


if __name__ == "__main__":
    unittest.main()
