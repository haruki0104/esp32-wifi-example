#!/usr/bin/env python3
"""Plot time-vs-amplitude charts from csi_time_log.py CSV files.

Single file: amp_mean per-frame line with baseline mean, cross-carrier std
band, and an RSSI subplot. Compare mode overlays two captures
(e.g. baseline vs waving) for discriminability checks. Heatmap mode draws
time (x) vs subcarrier (y) with posture segments shaded, correlating signal
with body posture; it needs a --per-subcarrier CSV (re-record or convert an
old JSONL with tools/csi_time_log.py --replay OLD.jsonl --per-subcarrier).

The tool prints one stats line per file and saves a PNG; it never shows a
window, so it works headless (CI, SSH).

Examples:
  python3 tools/csi_plot_csv.py captures/csi-amp-baseline-pos1.csv
  python3 tools/csi_plot_csv.py captures/csi-amp-baseline-pos1.csv \\
      --compare captures/csi-amp-waving-seated.csv -o /tmp/compare.png
  python3 tools/csi_plot_csv.py captures/csi-amp-pos2-posture.csv --heatmap \\
      --diff --mark "still:0-10,wave:10-30,still:30-40"
"""

import argparse
import csv
import statistics
import sys
from pathlib import Path


def load_series(path):
    """Load a csi_time_log.py CSV into a dict of lists."""
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"no data rows in {path}")
    t = [float(r["unix_time"]) for r in rows]
    t0 = t[0]
    return {
        "label": Path(path).stem,
        "ts": [x - t0 for x in t],
        "mean": [float(r["amp_mean"]) for r in rows],
        "std": [float(r["amp_std"]) for r in rows],
        "rssi": [int(r["rssi_dbm"]) for r in rows],
        "gaps": sum(int(r["seq_gap"]) for r in rows),
        "n": len(rows),
    }


def series_stats(series):
    """Robust summary numbers for one loaded series."""
    m = series["mean"]
    dur = series["ts"][-1] if len(series["ts"]) > 1 else 0.0
    return {
        "n": series["n"],
        "dur": dur,
        "fps": series["n"] / dur if dur > 0 else 0.0,
        "gaps": series["gaps"],
        "median": statistics.median(m),
        "mean": statistics.fmean(m),
        "tstd": statistics.pstdev(m),
        "min": min(m),
        "max": max(m),
        "over25": sum(1 for v in m if v > 25) / len(m) * 100,
        "rssi": statistics.fmean(series["rssi"]),
    }


def fmt_stats(name, stats):
    return (
        f"{name}: n={stats['n']} dur={stats['dur']:.1f}s "
        f"fps={stats['fps']:.1f} gaps={stats['gaps']} "
        f"median={stats['median']:.2f} mean={stats['mean']:.2f} "
        f"tstd={stats['tstd']:.2f} min={stats['min']:.2f} "
        f"max={stats['max']:.2f} over25={stats['over25']:.1f}% "
        f"rssi={stats['rssi']:.1f}"
    )


def _style_axes(ax1, ax2=None):
    ax1.set_ylabel("amplitude")
    ax1.grid(True, alpha=0.3)
    ax1.legend(fontsize=8, loc="upper right")
    if ax2 is not None:
        ax2.set_ylabel("RSSI (dBm)")
        ax2.set_xlabel("seconds")
        ax2.grid(True, alpha=0.3)


def plot_single(series, stats, title, out_path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True)
    ax1.plot(series["ts"], series["mean"], lw=0.8, label="amp_mean per frame")
    ax1.axhline(stats["mean"], color="r", ls="--", lw=1,
                label=f"mean={stats['mean']:.2f}")
    lo = [a - b for a, b in zip(series["mean"], series["std"])]
    hi = [a + b for a, b in zip(series["mean"], series["std"])]
    ax1.fill_between(series["ts"], lo, hi, alpha=0.2,
                     label="± cross-carrier std")
    ax1.set_title(title)
    ax2.plot(series["ts"], series["rssi"], lw=0.8, color="g")
    _style_axes(ax1, ax2)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return out_path


def plot_compare(first, first_stats, second, second_stats, title, out_path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True)
    ax1.plot(first["ts"], first["mean"], lw=0.7, color="gray", alpha=0.9,
             label=first["label"])
    ax1.plot(second["ts"], second["mean"], lw=0.8, color="tab:blue",
             label=second["label"])
    ax1.axhline(first_stats["median"], color="gray", ls=":", lw=1,
                label=f"{first['label']} median={first_stats['median']:.2f}")
    ax1.set_title(title)
    ax2.plot(first["ts"], first["rssi"], lw=0.7, color="gray", alpha=0.9)
    ax2.plot(second["ts"], second["rssi"], lw=0.8, color="tab:green")
    _style_axes(ax1, ax2)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return out_path


def default_output(input_path, compare_path=None):
    stem = Path(input_path).stem
    if compare_path:
        stem += f"-vs-{Path(compare_path).stem}"
    return str(Path(input_path).with_name(stem + ".png"))


def load_matrix(path):
    """Load per-subcarrier matrix (frames x carriers) plus base series."""
    series = load_series(path)
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    sc_cols = sorted(c for c in rows[0].keys()
                      if c.startswith("sc") and c[2:].isdigit())
    if not sc_cols:
        raise ValueError(f"{path} has no scNN columns; re-record or "
                         "convert with csi_time_log.py --per-subcarrier")
    import numpy as np

    matrix = np.array([[float(r[c]) if r[c] != "" else float("nan")
                        for c in sc_cols] for r in rows], dtype=float)
    return series, matrix, sc_cols


def time_edges(ts):
    """Cell edges for pcolormesh from per-row timestamps (seconds).

    Rows are NOT assumed evenly spaced: UDP gaps would otherwise skew the
    axis (frame index != time).
    """
    import numpy as np

    ts = [float(t) for t in ts]
    if not ts:
        raise ValueError("need at least one timestamp")
    if len(ts) == 1:
        half = 0.05
        return [ts[0] - half, ts[0] + half]
    edges = [ts[0] - (ts[1] - ts[0]) / 2.0]
    for i in range(1, len(ts)):
        edges.append((ts[i - 1] + ts[i]) / 2.0)
    edges.append(ts[-1] + (ts[-1] - ts[-2]) / 2.0)
    return edges


def parse_marks(spec):
    """Parse 'label:start-end,label:start-end' posture segments (seconds)."""
    marks = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            label, span = part.split(":")
            start, end = span.split("-")
            marks.append((label.strip(), float(start), float(end)))
        except ValueError:
            raise ValueError(f"bad mark segment: {part!r} "
                             "(want label:start-end)")
    if not marks:
        raise ValueError("empty --mark spec")
    return marks


def plot_heatmap(series, matrix, sc_cols, title, out_path,
                 diff_mode=False, vmax=None, marks=()):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    if diff_mode:
        base = np.nanmedian(matrix, axis=0)
        view = matrix - base
        cmap = "RdBu_r"
        cbar_label = "\u0394 amplitude vs carrier median"
        if vmax is None:
            m = float(np.nanpercentile(np.abs(view), 99))
            lo, hi = (-m, m) if m > 0 else (-1.0, 1.0)
        else:
            lo, hi = -vmax, vmax
    else:
        view = matrix
        cmap = "viridis"
        cbar_label = "amplitude"
        if vmax is None:
            lo = float(np.nanmin(view))
            hi = float(np.nanpercentile(view, 99))
            if hi <= lo:
                hi = lo + 1.0
        else:
            lo, hi = 0.0, vmax
    x_edges = time_edges(series["ts"]) if series["ts"] else [0.0, 1.0]
    y_edges = [float(i) - 0.5 for i in range(len(sc_cols) + 1)]
    fig, ax = plt.subplots(figsize=(11, 5.5))
    img = ax.pcolormesh(x_edges, y_edges, view.T, cmap=cmap,
                        shading="flat")
    img.set_clim(lo, hi)
    palette = ["#ffd54f", "#80deea", "#c5e1a5", "#ce93d8", "#ffab91"]
    for i, (label, start, end) in enumerate(marks):
        ax.axvspan(start, end, color=palette[i % len(palette)], alpha=0.25)
        ax.text((start + end) / 2, len(sc_cols) - 1.5, label, ha="center",
                va="top", fontsize=9,
                bbox=dict(boxstyle="round", facecolor="white", alpha=0.7))
    ax.set_xlabel("seconds")
    ax.set_ylabel("subcarrier index")
    ax.set_title(title)
    fig.colorbar(img, ax=ax, label=cbar_label)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return out_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", type=Path, help="csi_time_log.py output CSV")
    parser.add_argument("--compare", type=Path, default=None,
                        help="second CSV to overlay (e.g. waving vs baseline)")
    parser.add_argument("--heatmap", action="store_true",
                        help="time-vs-subcarrier heatmap (needs a "
                             "--per-subcarrier CSV) with optional --mark")
    parser.add_argument("--diff", action="store_true",
                        help="heatmap shows deviation from per-carrier "
                             "median (diverging colors)")
    parser.add_argument("--vmax", type=float, default=None,
                        help="fixed heatmap scale: 0..vmax, or +-vmax "
                             "with --diff")
    parser.add_argument("--mark", default=None,
                        help="posture segments 'label:start-end,...' "
                             "shaded on the heatmap, e.g. "
                             "'still:0-10,wave:10-30,still:30-40'")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="PNG path (default: alongside input)")
    parser.add_argument("--title", default=None, help="chart title override")
    args = parser.parse_args(argv)

    try:
        first = load_series(args.csv)
    except (OSError, ValueError, KeyError) as error:
        parser.error(f"cannot load {args.csv}: {error}")
    first_stats = series_stats(first)
    print(fmt_stats(first["label"], first_stats))

    if args.heatmap and args.compare is not None:
        parser.error("--heatmap and --compare are mutually exclusive")
    if args.vmax is not None and args.vmax <= 0:
        parser.error("--vmax must be positive")

    if args.heatmap:
        try:
            marks = parse_marks(args.mark) if args.mark else []
        except ValueError as error:
            parser.error(str(error))
        try:
            series, matrix, sc_cols = load_matrix(args.csv)
        except (OSError, ValueError, KeyError) as error:
            parser.error(f"cannot load {args.csv}: {error}")
        mode = " (\u0394 carrier median)" if args.diff else ""
        title = args.title or f"{first['label']} time-vs-subcarrier{mode}"
        stem = Path(args.csv).stem + ("-diff" if args.diff else "")
        out = (str(args.output) if args.output
               else str(Path(args.csv).with_name(stem + "-heatmap.png")))
        plot_heatmap(series, matrix, sc_cols, title, out,
                     diff_mode=args.diff, vmax=args.vmax, marks=marks)
        print(f"saved {out}")
        return 0

    if args.compare is None:
        title = args.title or (
            f"CSI baseline — {first['label']} "
            f"({first_stats['n']} frames, {first_stats['dur']:.0f}s)")
        out = str(args.output) if args.output else default_output(args.csv)
        plot_single(first, first_stats, title, out)
    else:
        try:
            second = load_series(args.compare)
        except (OSError, ValueError, KeyError) as error:
            parser.error(f"cannot load {args.compare}: {error}")
        second_stats = series_stats(second)
        print(fmt_stats(second["label"], second_stats))
        title = args.title or f"{first['label']} vs {second['label']}: amp_mean over time"
        out = str(args.output) if args.output else default_output(
            args.csv, args.compare)
        plot_compare(first, first_stats, second, second_stats, title, out)
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
