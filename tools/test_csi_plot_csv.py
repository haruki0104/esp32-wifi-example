import csv
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from csi_plot_csv import (default_output, load_matrix, load_series, parse_marks,
                           plot_compare, plot_heatmap, plot_single, series_stats)


def write_csv(path, n=10, start_seq=0, mean_base=16.0):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["captured_at_utc", "unix_time", "sequence", "seq_gap",
                         "rssi_dbm", "channel", "source_mac", "n_subcarriers",
                         "amp_mean", "amp_std", "amp_min", "amp_max",
                         "fw_drops", "sender_ip"])
        for i in range(n):
            writer.writerow(["2026-01-01T00:00:00+00:00", 1700000000.0 + i * 0.1,
                             start_seq + i, 0, -30, 10, "aa:bb:cc:dd:ee:ff",
                             62, mean_base + i * 0.1, 5.0, 1.0, 30.0, 0,
                             "192.168.212.117"])


def write_per_carrier_csv(path, n=8, carriers=4):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["captured_at_utc", "unix_time", "sequence", "seq_gap",
                         "rssi_dbm", "channel", "source_mac", "n_subcarriers",
                         "amp_mean", "amp_std", "amp_min", "amp_max",
                         "fw_drops", "sender_ip"] +
                        [f"sc{i:02d}" for i in range(carriers)])
        for i in range(n):
            writer.writerow(["2026-01-01T00:00:00+00:00", 1700000000.0 + i * 0.1,
                             i, 0, -30, 10, "aa:bb:cc:dd:ee:ff", carriers,
                             15.0, 5.0, 1.0, 30.0, 0, "192.168.212.117"] +
                            [10.0 + i + j for j in range(carriers)])


class PlotCsvTests(unittest.TestCase):
    def test_load_and_stats(self):
        with TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "amp.csv")
            write_csv(path, n=10)
            series = load_series(path)
            self.assertEqual(series["n"], 10)
            self.assertAlmostEqual(series["ts"][0], 0.0)
            stats = series_stats(series)
            self.assertEqual(stats["gaps"], 0)
            self.assertGreater(stats["fps"], 0)
            self.assertAlmostEqual(stats["median"], 16.45)

    def test_load_rejects_empty(self):
        with TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "empty.csv")
            write_csv(path, n=0)
            with self.assertRaises(ValueError):
                load_series(path)

    def test_plot_single_writes_png(self):
        with TemporaryDirectory() as tmp:
            src = str(Path(tmp) / "amp.csv")
            write_csv(src)
            series = load_series(src)
            out = str(Path(tmp) / "amp.png")
            plot_single(series, series_stats(series), "title", out)
            self.assertTrue(Path(out).exists())
            self.assertGreater(Path(out).stat().st_size, 0)

    def test_plot_compare_writes_png(self):
        with TemporaryDirectory() as tmp:
            a = str(Path(tmp) / "a.csv")
            b = str(Path(tmp) / "b.csv")
            write_csv(a, n=10, mean_base=16.0)
            write_csv(b, n=8, mean_base=20.0)
            sa, sb = load_series(a), load_series(b)
            out = str(Path(tmp) / "compare.png")
            plot_compare(sa, series_stats(sa), sb, series_stats(sb),
                         "title", out)
            self.assertTrue(Path(out).exists())

    def test_parse_marks(self):
        marks = parse_marks("still:0-10,wave:10-30,still:30-40")
        self.assertEqual(marks, [("still", 0.0, 10.0), ("wave", 10.0, 30.0),
                                 ("still", 30.0, 40.0)])
        with self.assertRaises(ValueError):
            parse_marks("wave-10-30")
        with self.assertRaises(ValueError):
            parse_marks("  ")

    def test_load_matrix_needs_sc_columns(self):
        with TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "amp.csv")
            write_csv(path, n=5)
            with self.assertRaises(ValueError):
                load_matrix(path)

    def test_time_edges_follow_timestamps(self):
        from csi_plot_csv import time_edges
        self.assertEqual(time_edges([1.0]), [0.95, 1.05])
        edges = time_edges([0.0, 0.1, 0.3])  # gap before last row
        self.assertEqual(len(edges), 4)
        self.assertAlmostEqual(edges[0], -0.05)
        self.assertAlmostEqual(edges[2], 0.2)  # midpoint, not 0.15
        self.assertAlmostEqual(edges[3], 0.4)
        with self.assertRaises(ValueError):
            time_edges([])

    def test_heatmap_modes_write_png(self):
        with TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "amp.csv")
            write_per_carrier_csv(path)
            series, matrix, sc_cols = load_matrix(path)
            self.assertEqual(matrix.shape, (8, 4))
            self.assertEqual(sc_cols, ["sc00", "sc01", "sc02", "sc03"])
            for diff in (False, True):
                out = str(Path(tmp) / f"heat-{diff}.png")
                plot_heatmap(series, matrix, sc_cols, "t", out,
                             diff_mode=diff, vmax=30.0,
                             marks=[("wave", 0.2, 0.5)])
                self.assertTrue(Path(out).exists())

    def test_default_output_names(self):
        self.assertEqual(default_output("captures/a.csv"), "captures/a.png")
        self.assertEqual(default_output("captures/a.csv", "captures/b.csv"),
                         "captures/a-vs-b.png")


if __name__ == "__main__":
    unittest.main()
