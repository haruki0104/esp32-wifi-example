import unittest

import numpy as np

from csi_detect import (LiveDetector, detect, score_against_marks,
                         trailing_stat)


def synthetic(n=300, fps=10.0, carriers=8, burst_at=(10.0, 20.0),
              burst_gain=25.0, seed=0):
    rng = np.random.default_rng(seed)
    base = 15.0 + rng.normal(0, 1.0, size=(n, carriers))
    times = np.arange(n) / fps
    s, e = burst_at
    active = (times >= s) & (times < e)
    base[active, :carriers // 2] += burst_gain
    return base, times


class TrailingStatTests(unittest.TestCase):
    def test_trailing_window(self):
        values = np.array([1.0, 2.0, 3.0, 4.0])
        times = np.array([0.0, 1.0, 2.0, 3.0])
        out = trailing_stat(values, times, 1.5, np.mean)
        # t=3 window covers [1.5, 3] -> mean(3, 4).
        self.assertAlmostEqual(out[3], 3.5)
        self.assertAlmostEqual(out[0], 1.0)


class DetectTests(unittest.TestCase):
    def test_flags_burst_bout(self):
        # Baseline window must exceed the event: otherwise the trailing
        # baseline absorbs a long bout (by design, not a bug).
        matrix, times = synthetic()
        segments, z, _ = detect(matrix, times, base_s=30.0, short_s=1.0)
        self.assertTrue(segments, "expected at least one detection")
        # Bout is 10-20s; allow timing tolerance.
        hit = any(s <= 13.0 and e >= 17.0 for s, e in segments)
        self.assertTrue(hit, f"segments {segments} miss the 10-20s bout")

    def test_quiet_file_has_no_detection(self):
        matrix, times = synthetic(burst_gain=0.0)
        segments, _, _ = detect(matrix, times, base_s=8.0, short_s=1.0)
        self.assertEqual(segments, [])

    def test_live_detector_flags_bout(self):
        matrix, times = synthetic(n=300, fps=10.0)
        det = LiveDetector(base_s=30.0, short_s=1.0, warmup_s=5.0)
        states = [det.update(row, float(t)) for row, t in zip(matrix, times)]
        zs = [z for z, _ in states]
        act = [a for _, a in states]
        # Active at some point inside the 10-20s bout, quiet at the end.
        self.assertTrue(any(act[100:200]), "never went active in bout")
        self.assertFalse(any(act[250:]), "still active after bout")
        self.assertGreater(max(zs[100:200]), 3.0)

    def test_live_detector_quiet_stays_quiet(self):
        matrix, times = synthetic(n=150, fps=10.0, burst_gain=0.0)
        det = LiveDetector(base_s=30.0, short_s=1.0, warmup_s=5.0)
        act = [det.update(row, float(t))[1]
               for row, t in zip(matrix, times)]
        self.assertFalse(any(act))

    def test_scoring(self):
        segments = [(11.0, 19.0)]
        marks = [("still", 0.0, 10.0), ("wave", 10.0, 20.0),
                 ("still", 20.0, 30.0)]
        score = score_against_marks(segments, marks, 30.0)
        self.assertAlmostEqual(score["wave_recall"], 0.8)
        self.assertAlmostEqual(score["still_fpr"], 0.0)


if __name__ == "__main__":
    unittest.main()
