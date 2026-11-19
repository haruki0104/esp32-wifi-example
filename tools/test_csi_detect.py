import unittest
from pathlib import Path

try:
    import numpy as np
except ImportError as error:
    # The detector needs the host plot stack; without it the documented
    # verify command must report a skip, not a pile of errors.
    raise unittest.SkipTest(
        f"host analysis deps missing ({error.name}); "
        "install tools/requirements-plot.txt"
    ) from error

from csi_detect import (DETECT_DEFAULTS, LiveDetector, default_config_path,
                         detect, load_config_file, resolve_params,
                         score_against_marks, trailing_stat)


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

    def test_config_file_overrides_defaults(self):
        import json
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "custom.json"
            path.write_text(json.dumps({"z_enter": 9.0}), encoding="utf-8")
            params = resolve_params({}, path)
            self.assertEqual(params["z_enter"], 9.0)
            self.assertEqual(params["z_exit"], DETECT_DEFAULTS["z_exit"])

    def test_cli_wins_over_config_file(self):
        import json
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "custom.json"
            path.write_text(json.dumps({"z_enter": 9.0, "z_exit": 0.1}),
                              encoding="utf-8")
            params = resolve_params({"z_enter": 2.0, "z_exit": None}, path)
            self.assertEqual(params["z_enter"], 2.0)
            self.assertEqual(params["z_exit"], 0.1)

    def test_config_rejects_unknown_keys(self):
        import json
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(json.dumps({"nope": 1.0}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config_file(path)
            with self.assertRaises(ValueError):
                resolve_params({}, path)

    def test_explicit_missing_config_raises(self):
        with self.assertRaises(ValueError):
            resolve_params({}, "/tmp/does-not-exist-csi-detect.json")

    def test_detector_resets_on_width_change(self):
        matrix8, times = synthetic(n=20, fps=10.0, carriers=8)
        det = LiveDetector(base_s=30.0, short_s=1.0, warmup_s=0.0)
        for row, t in zip(matrix8, times):
            det.update(list(row), float(t))
        narrow = [1.0, 2.0, 3.0, 4.0]
        z, live = det.update(narrow, float(times[-1]) + 0.1)
        self.assertTrue(abs(z) < 1e9 and z == z)  # finite, no raise
        self.assertFalse(live)

    def test_shipped_config_loads(self):
        params = resolve_params({})
        self.assertEqual(set(params), set(DETECT_DEFAULTS))
        for value in params.values():
            self.assertGreater(value, 0)

    def test_scoring(self):
        segments = [(11.0, 19.0)]
        marks = [("still", 0.0, 10.0), ("wave", 10.0, 20.0),
                 ("still", 20.0, 30.0)]
        score = score_against_marks(segments, marks, 30.0)
        self.assertAlmostEqual(score["wave_recall"], 0.8)
        self.assertAlmostEqual(score["still_fpr"], 0.0)


if __name__ == "__main__":
    unittest.main()
