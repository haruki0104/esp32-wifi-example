#!/usr/bin/env python3
"""Adaptive CSI activity detector (offline prototype).

Reads a csi_time_log.py --per-subcarrier CSV and flags activity without any
fixed amplitude threshold: each frame gets an energy score (mean absolute
deviation from the *trailing* per-carrier median), smoothed over a short
window and compared against a slow trailing baseline via a z-score with
hysteresis. Background drift therefore moves the baseline instead of
tripping the detector.

Energy beats burst-counting here: seated waving shows up as a few strong
per-carrier deviations on a quiet background, while background spikes are
numerous but weak -- counting carriers weights them equally, energy does
not.

With --mark the detections are scored against posture segments
(still-vs-wave fraction flagged) for offline validation:

  python3 tools/csi_detect.py captures/csi-amp-pos3-posture.csv \\
      --mark "still:0-10,wave:10-30,still:30-40" --plot /tmp/det.png
"""

import argparse
import sys
from collections import deque
from pathlib import Path

from csi_plot_csv import load_matrix, parse_marks


def trailing_median_mad(matrix, times, window_s, min_frames=5):
    """Per-carrier trailing median and MAD for every frame.

    Row i uses matrix[max(0, t_i - window_s) : i] (strictly past frames);
    early rows fall back to whatever history exists.
    """
    import numpy as np

    n = matrix.shape[0]
    meds = np.empty_like(matrix)
    mads = np.empty_like(matrix)
    for i in range(n):
        lo = 0
        cutoff = times[i] - window_s
        while lo < i and times[lo] < cutoff:
            lo += 1
        win = matrix[lo:i] if i - lo >= 1 else matrix[max(0, i - 1):i + 1]
        if win.shape[0] < 1:
            win = matrix[i:i + 1]
        med = np.median(win, axis=0)
        meds[i] = med
        mads[i] = np.median(np.abs(win - med), axis=0)
    return meds, mads


def trailing_stat(values, times, window_s, fn):
    """Apply fn over the trailing window [t_i - window_s, t_i]."""
    import numpy as np

    values = np.asarray(values, dtype=float)
    out = np.empty_like(values)
    for i in range(len(values)):
        lo = 0
        cutoff = times[i] - window_s
        while lo <= i and times[lo] < cutoff:
            lo += 1
        out[i] = float(fn(values[lo:i + 1]))
    return out


def trailing_smooth(values, times, window_s):
    """Trailing mean of a 1-D series over window_s seconds."""
    import numpy as np

    return trailing_stat(values, times, window_s, np.mean)


def detect(matrix, times, base_s=60.0, short_s=3.0,
           z_enter=3.0, z_exit=1.5, min_dur_s=2.0,
           merge_gap_s=2.0, warmup_s=5.0):
    """Return (segments, score, z, info). Segments are (start, end) seconds.

    Detections starting before warmup_s are dropped: with almost no
    trailing history the carrier baselines are noisy and the first seconds
    always look anomalous.
    """
    import numpy as np

    times = np.asarray(times, dtype=float)
    meds, _ = trailing_median_mad(matrix, times, base_s)
    energy = np.mean(np.abs(matrix - meds), axis=1)
    # Median (not mean): single-frame environmental spikes must not move
    # the short window, or they dominate the scale and mask real bouts.
    smooth = trailing_stat(energy, times, short_s, np.median)
    ref_med = trailing_stat(smooth, times, base_s, np.median)
    # Robust scale of the smoothed score around its trailing median.
    resid = np.abs(smooth - ref_med)
    ref_mad = trailing_stat(resid, times, base_s, np.median)
    scale = 1.4826 * ref_mad + 1e-6
    z = (smooth - ref_med) / scale

    # Hysteresis state machine on z.
    active = False
    start = 0.0
    segments = []
    for i in range(len(times)):
        if not active and z[i] >= z_enter:
            active = True
            start = float(times[i])
        elif active and z[i] <= z_exit:
            active = False
            segments.append((start, float(times[i])))
    if active:
        segments.append((start, float(times[-1])))

    # Merge close segments, drop short ones.
    merged = []
    for s, e in segments:
        if merged and s - merged[-1][1] <= merge_gap_s:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    segments = [(s, e) for s, e in merged
                if e - s >= min_dur_s and s >= warmup_s]
    info = {
        "energy": energy,
        "smooth": smooth,
        "ref_med": ref_med,
        "z_enter": z_enter,
        "z_exit": z_exit,
    }
    return segments, z, info


class LiveDetector:
    """Incremental twin of detect() for the live plotter.

    Same algorithm -- per-frame energy vs trailing carrier medians,
    short-window median smoothing, z-score vs trailing baseline with
    hysteresis -- but updated one spectrum at a time over bounded deques
    so memory stays flat. Feed every new spectrum with its timestamp;
    read z/active for display. Display-grade: the offline detect() on a
    full CSV remains the reference for scoring.
    """

    def __init__(self, base_s=60.0, short_s=3.0, z_enter=3.0, z_exit=1.5,
                 min_dur_s=2.0, warmup_s=5.0, fps_hint=15.0):
        capacity = max(32, int(base_s * fps_hint) + 8)
        self.base_s = base_s
        self.short_s = short_s
        self.z_enter = z_enter
        self.z_exit = z_exit
        self.min_dur_s = min_dur_s
        self.warmup_s = warmup_s
        self.times = deque(maxlen=capacity)
        self.spectra = deque(maxlen=capacity)
        self.energy_hist = deque(maxlen=capacity)
        self.short_hist = deque(maxlen=capacity)
        self.t0 = None
        self.active = False
        self.active_since = 0.0
        self.z = 0.0

    def update(self, spectrum, t):
        """Feed one spectrum (list of floats) at time t; returns (z, live)."""
        import numpy as np

        spec = np.asarray(list(spectrum), dtype=float)
        if self.t0 is None:
            self.t0 = float(t)
        self.times.append(float(t))
        self.spectra.append(spec)
        arr = np.array(self.spectra)
        ts = np.array(self.times)
        past = ts < float(t)
        if np.any(past):
            win = arr[past]
            win = win[ts[past] >= float(t) - self.base_s]
        else:
            win = np.empty((0, spec.shape[0]))
        if win.shape[0] == 0:
            meds = spec
        else:
            meds = np.median(win, axis=0)
        energy = float(np.mean(np.abs(spec - meds)))
        self.energy_hist.append(energy)
        eh = np.array(self.energy_hist)
        recent = eh[ts >= float(t) - self.short_s]
        short_med = float(np.median(recent)) if recent.size else energy
        self.short_hist.append(short_med)
        sh = np.array(self.short_hist)
        base_win = sh[ts >= float(t) - self.base_s]
        if base_win.size >= 3:
            ref_med = float(np.median(base_win))
            ref_mad = float(np.median(np.abs(base_win - ref_med)))
        else:
            ref_med, ref_mad = short_med, 0.0
        scale = 1.4826 * ref_mad + 1e-6
        z = (short_med - ref_med) / scale
        if float(t) - self.t0 < self.warmup_s:
            z = 0.0
        self.z = float(z)
        if not self.active and self.z >= self.z_enter:
            self.active = True
            self.active_since = float(t)
        elif self.active and self.z <= self.z_exit:
            self.active = False
        live = bool(self.active and
                    float(t) - self.active_since >= self.min_dur_s)
        return self.z, live


def score_against_marks(segments, marks, total_dur):
    """Fraction of marked wave-time and still-time covered by detections."""
    def covered(a, b):
        return sum(max(0.0, min(e, b) - max(s, a)) for s, e in segments)

    wave_t = sum(e - s for label, s, e in marks if "wave" in label.lower())
    still_t = sum(e - s for label, s, e in marks if "wave" not in label.lower())
    wave_hit = sum(covered(s, e) for label, s, e in marks
                   if "wave" in label.lower())
    still_hit = sum(covered(s, e) for label, s, e in marks
                    if "wave" not in label.lower())
    return {
        "wave_recall": wave_hit / wave_t if wave_t else 0.0,
        "still_fpr": still_hit / still_t if still_t else 0.0,
    }


def plot_detection(times, info_z, info, segments, marks, title, out_path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(times, info_z, lw=0.8, label="z-score (short vs trailing)")
    ax.axhline(info["z_enter"], color="r", ls="--", lw=1, label="enter")
    ax.axhline(info["z_exit"], color="r", ls=":", lw=1, label="exit")
    for s, e in segments:
        ax.axvspan(s, e, color="tab:red", alpha=0.2)
    palette = ["#ffd54f", "#80deea", "#c5e1a5", "#ce93d8"]
    for i, (label, s, e) in enumerate(marks):
        ax.axvspan(s, e, color=palette[i % len(palette)], alpha=0.12)
        ax.text((s + e) / 2, 0.95, label, ha="center", va="top",
                fontsize=8, transform=ax.get_xaxis_transform())
    ax.set_xlabel("seconds")
    ax.set_ylabel("z")
    ax.set_title(title)
    ax.legend(fontsize=8, loc="upper right")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return out_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--base", type=float, default=60.0,
                        help="trailing baseline window in seconds (default: 60)")
    parser.add_argument("--short", type=float, default=3.0,
                        help="detection smoothing window in seconds (default: 3)")
    parser.add_argument("--z-enter", type=float, default=3.0)
    parser.add_argument("--z-exit", type=float, default=1.5)
    parser.add_argument("--min-dur", type=float, default=2.0,
                        help="drop detections shorter than this (seconds)")
    parser.add_argument("--warmup", type=float, default=5.0,
                        help="ignore detections starting before this (seconds)")
    parser.add_argument("--mark", default=None,
                        help="ground-truth 'label:start-end,...' for scoring")
    parser.add_argument("--plot", type=Path, default=None)
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="write detected segments to a text file")
    args = parser.parse_args(argv)

    try:
        series, matrix, _ = load_matrix(args.csv)
    except (OSError, ValueError, KeyError) as error:
        parser.error(f"cannot load {args.csv}: {error}")
    marks = []
    if args.mark:
        try:
            marks = parse_marks(args.mark)
        except ValueError as error:
            parser.error(str(error))

    import numpy as np

    times = np.array(series["ts"], dtype=float)
    segments, z, info = detect(matrix, times, base_s=args.base,
                               short_s=args.short, z_enter=args.z_enter,
                               z_exit=args.z_exit, min_dur_s=args.min_dur,
                               warmup_s=args.warmup)
    print(f"{args.csv}: {len(segments)} segment(s)")
    for s, e in segments:
        print(f"  active {s:.1f}s - {e:.1f}s ({e - s:.1f}s)")
    if marks:
        total = times[-1] if len(times) else 0.0
        sc = score_against_marks(segments, marks, total)
        print(f"  wave recall={sc['wave_recall']*100:.0f}% "
              f"still fpr={sc['still_fpr']*100:.0f}%")
    if args.plot:
        plot_detection(list(times), z, info, segments, marks,
                       f"{Path(args.csv).stem} adaptive detection",
                       str(args.plot))
        print(f"saved {args.plot}")
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            for s, e in segments:
                handle.write(f"{s:.2f} {e:.2f}\n")
        print(f"saved {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
