import unittest
from unittest import mock

try:
    import numpy  # noqa: F401  (waterfall_view is numpy-based)
except ImportError as error:
    raise unittest.SkipTest(
        f"host analysis deps missing ({error.name}); "
        "install tools/requirements-plot.txt"
    ) from error

from csi_live_plot import LiveState, rolling_xlim, run_plot, waterfall_view


class WaterfallViewTests(unittest.TestCase):
    def test_auto_scale_uses_window_min_to_p99(self):
        arr = [[10.0, 20.0, 30.0], [11.0, 21.0, 31.0]]
        display, lo, hi, diverging = waterfall_view(arr)
        self.assertFalse(diverging)
        self.assertEqual(display.tolist(), [list(map(float, r)) for r in arr])
        self.assertAlmostEqual(lo, 10.0)
        self.assertGreater(hi, lo)

    def test_fixed_scale_is_zero_to_vmax(self):
        arr = [[10.0, 20.0], [30.0, 40.0]]
        display, lo, hi, diverging = waterfall_view(arr, vmax=40.0)
        self.assertFalse(diverging)
        self.assertEqual((lo, hi), (0.0, 40.0))
        self.assertEqual(display.shape, (2, 2))

    def test_diff_centers_on_baseline(self):
        # Baseline rows sit at 20; newest row jumps by +5 on one carrier.
        arr = [[20.0, 20.0]] * 4 + [[25.0, 20.0]]
        display, lo, hi, diverging = waterfall_view(arr, diff_mode=True)
        self.assertTrue(diverging)
        self.assertAlmostEqual(display[-1][0], 5.0)
        self.assertAlmostEqual(display[-1][1], 0.0)
        # Symmetric scale around zero.
        self.assertAlmostEqual(lo, -hi)
        self.assertGreater(hi, 0)

    def test_diff_fixed_scale_is_symmetric(self):
        arr = [[20.0, 20.0], [25.0, 15.0]]
        _, lo, hi, diverging = waterfall_view(arr, vmax=15.0, diff_mode=True)
        self.assertTrue(diverging)
        self.assertEqual((lo, hi), (-15.0, 15.0))

    def test_diff_single_row_does_not_crash(self):
        display, lo, hi, diverging = waterfall_view([[7.0, 8.0]], diff_mode=True)
        self.assertTrue(diverging)
        self.assertEqual(display.shape, (1, 2))
        self.assertLess(lo, hi)

    def test_rolling_xlim_grows_then_scrolls(self):
        self.assertEqual(rolling_xlim(10.0, 60.0), (0.0, 60.0))
        self.assertEqual(rolling_xlim(60.0, 60.0), (0.0, 60.0))
        self.assertEqual(rolling_xlim(90.0, 60.0), (30.0, 90.0))
        with self.assertRaises(ValueError):
            rolling_xlim(10.0, 0.0)

    def test_new_spectra_since_survives_window_overflow(self):
        from csi_live_plot import new_spectra_since
        # Window holds 3, 5 frames pushed total, 2 already fed.
        fresh, fed = new_spectra_since([3, 4, 5], 5, 2)
        self.assertEqual(list(fresh), [3, 4, 5])
        self.assertEqual(fed, 5)
        # Nothing new on the next tick.
        fresh, fed = new_spectra_since([3, 4, 5], 5, 5)
        self.assertEqual(list(fresh), [])
        self.assertEqual(fed, 5)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            waterfall_view([])
        with self.assertRaises(ValueError):
            waterfall_view([[1.0, 2.0]], vmax=0.0)
        with self.assertRaises(ValueError):
            waterfall_view([[1.0, 2.0]], vmax=-5.0)


class LiveFeedSteppingTests(unittest.TestCase):
    def test_detector_keeps_advancing_after_feed_window_fills(self):
        import matplotlib

        matplotlib.use("Agg", force=True)
        state = LiveState(window=8)
        animation = {}

        class SteppingAnimation:
            def __init__(self, figure, update, **_kwargs):
                animation["figure"] = figure
                for tick in range(120):
                    # The UDP worker drops empty TX heartbeats before LiveState.
                    if tick % 30:
                        level = 10.0 if tick < 60 or tick % 2 else 40.0
                        state.push_spectrum(
                            [level, level + 1.0, level + 2.0, level + 3.0],
                            {"sequence": tick - tick // 30,
                             "rssi_dbm": -45, "channel": 6,
                             "source_mac": "02:00:00:00:00:01"},
                        )
                    update(tick)

        clock = (step * 0.1 for step in range(1000))
        detect_params = {
            "base_s": 3.0,
            "short_s": 0.3,
            "z_enter": 3.0,
            "z_exit": 1.5,
            "min_dur_s": 0.2,
            "warmup_s": 0.0,
        }
        with mock.patch("matplotlib.animation.FuncAnimation", SteppingAnimation), \
             mock.patch("matplotlib.pyplot.show"), \
             mock.patch("csi_live_plot.time.monotonic",
                        side_effect=lambda: next(clock)):
            run_plot(state, window=8, interval_ms=100, detect_mode=True,
                     detect_params=detect_params)

        figure = animation["figure"]
        z_values = list(figure.axes[2].lines[0].get_ydata())
        _, _, stats = state.snapshot()
        self.assertEqual(stats["frames"], 116)
        # The first tick is a heartbeat with no plot data yet; later skipped
        # heartbeats redraw the existing state without becoming CSI frames.
        self.assertEqual(len(z_values), 119)
        self.assertTrue(any(abs(z) > 0.1 for z in z_values[80:]))


if __name__ == "__main__":
    unittest.main()
