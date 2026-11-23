#!/usr/bin/env python3
"""Live CSI waveform plot from the ESP32 UDP stream.

Subscribes to the same CSI1 UDP datagrams as tools/csi_udp_read.py and draws,
in real time:

  1. per-frame spectrum line: x = subcarrier index, y = amplitude
     (amplitude = sqrt(real^2 + imag^2), payload order is imag byte then
     real byte, signed int8; first 4 bytes skipped when first_word_invalid)
     The top line always shows the raw latest spectrum.
  2. scrolling waterfall: x = subcarrier index, y = recent frame sequence.
     By default color = amplitude with a per-frame auto scale (window min
     to 99th percentile). Use --vmax for a fixed 0..vmax scale so temporal
     changes stay visible instead of being renormalized away. Use --diff to
     paint the deviation from the window baseline (mean of all but the
     newest frame) with a diverging colormap; --vmax then means a fixed
     symmetric +-vmax scale. The two flags are independent and compose.

Network receive runs in a background thread with a bounded deque, so a slow
GUI never blocks the UDP socket. Optional --output archives the same JSONL
records as tools/csi_udp_read.py while plotting.

Examples:
  python3 tools/csi_live_plot.py --port 5005
  python3 tools/csi_live_plot.py --port 5005 --vmax 40
  python3 tools/csi_live_plot.py --port 5005 --diff --vmax 15
  python3 tools/csi_live_plot.py --port 5005 --window 200 --output captures/live.jsonl
  python3 tools/csi_live_plot.py --replay captures/csi-20261108T014649Z.jsonl
"""

import argparse
import json
import socket
import sys
import threading
import time
from collections import deque
from pathlib import Path

from csi_detect import LiveDetector, resolve_params
from csi_frame import amplitude_spectrum
from csi_read import frame_to_record
from csi_udp_read import parse_frame_datagram


class LiveState:
    """Thread-safe rolling window shared between UDP thread and GUI thread."""

    def __init__(self, window=100):
        self.lock = threading.Lock()
        self.window = window
        self.spectra = deque(maxlen=window)  # list[float] per frame
        self.n_subcarriers = 0
        self.latest_meta = {}
        self.frames = 0
        self.malformed = 0
        self.gaps = 0
        self.fw_drops = 0
        self.last_seq = None
        self.arrival_times = deque(maxlen=30)
        self.fps = 0.0

    def push_spectrum(self, spectrum, meta):
        with self.lock:
            if self.n_subcarriers and len(spectrum) != self.n_subcarriers:
                # Payload size changed: restart the waterfall.
                self.spectra.clear()
            self.n_subcarriers = len(spectrum)
            self.spectra.append(list(spectrum))
            self.latest_meta = dict(meta)
            self.frames += 1
            now = time.monotonic()
            self.arrival_times.append(now)
            if len(self.arrival_times) >= 2:
                span = self.arrival_times[-1] - self.arrival_times[0]
                if span > 0:
                    self.fps = (len(self.arrival_times) - 1) / span
            seq = meta.get("sequence")
            if seq is not None:
                if self.last_seq is not None:
                    expected = (self.last_seq + 1) & 0xFFFFFFFF
                    if seq != expected:
                        # UDP loss or restart; count one gap event.
                        # Forward jumps only (ignore old duplicates).
                        diff = (seq - self.last_seq) & 0xFFFFFFFF
                        if 0 < diff < 0x80000000:
                            self.gaps += 1
                self.last_seq = seq
            self.fw_drops += int(meta.get("firmware_drops_since_previous_frame", 0) or 0)

    def note_malformed(self):
        with self.lock:
            self.malformed += 1

    def snapshot(self):
        with self.lock:
            return (
                [list(s) for s in self.spectra],
                dict(self.latest_meta),
                {
                    "frames": self.frames,
                    "malformed": self.malformed,
                    "gaps": self.gaps,
                    "fw_drops": self.fw_drops,
                    "fps": self.fps,
                    "last_seq": self.last_seq,
                },
            )


def waterfall_view(arr, vmax=None, diff_mode=False):
    """Compute the waterfall display array and color limits.

    arr: 2D (frames, subcarriers) float array, oldest row first.
    Returns (display, lo, hi, diverging).
    - Normal mode: display is the raw amplitudes; auto scale uses the
      window min to 99th percentile, fixed scale uses (0, vmax).
    - Diff mode: display is each row minus the baseline (per-subcarrier
      mean of all rows but the newest); auto scale is symmetric (-m, m)
      from the 99th percentile of |display|, fixed scale is (-vmax, vmax).
    """
    import numpy as np

    arr = np.asarray(arr, dtype=float)
    if arr.ndim != 2 or arr.shape[0] == 0 or arr.shape[1] == 0:
        raise ValueError("waterfall needs a non-empty 2D array")
    if vmax is not None and vmax <= 0:
        raise ValueError("vmax must be positive")
    if not diff_mode:
        if vmax is None:
            lo, hi = float(np.min(arr)), float(np.percentile(arr, 99))
            if hi <= lo:
                hi = lo + 1.0
        else:
            lo, hi = 0.0, float(vmax)
        return arr, lo, hi, False
    if arr.shape[0] >= 2:
        baseline = np.mean(arr[:-1], axis=0)
    else:
        baseline = np.zeros(arr.shape[1])
    display = arr - baseline
    if vmax is None:
        m = float(np.percentile(np.abs(display), 99))
        if m <= 0:
            m = 1.0
        lo, hi = -m, m
    else:
        lo, hi = -float(vmax), float(vmax)
    return display, lo, hi, True


def new_spectra_since(spectra, frames_total, fed_total):
    """Return (new_spec_list, fed_total) using a monotonic counter.

    snapshot() only holds the rolling window, so window length cannot tell
    new frames from old once the deque is full; the cumulative pushed
    count can.
    """
    new_count = max(0, int(frames_total) - int(fed_total))
    return spectra[max(0, len(spectra) - new_count):], int(frames_total)


def rolling_xlim(t_last, span_s):
    """Fixed-width x window ending at t_last (scrolls once filled)."""
    span = float(span_s)
    if span <= 0:
        raise ValueError("span must be positive")
    if t_last <= span:
        return (0.0, span)
    return (float(t_last) - span, float(t_last))


def udp_worker(state, listen, port, chip, stop_event, output_file=None):
    """Background thread: recv CSI1 datagrams, push spectra, optionally archive."""
    out_handle = None
    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        out_handle = output_file.open("a", encoding="utf-8")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            from csi_read import MAX_FRAME_SIZE

            sock.bind((listen, port))
            sock.settimeout(0.25)
            while not stop_event.is_set():
                try:
                    datagram, addr = sock.recvfrom(MAX_FRAME_SIZE + 1)
                except socket.timeout:
                    continue
                try:
                    frame = parse_frame_datagram(datagram, chip)
                except ValueError:
                    state.note_malformed()
                    continue
                # TX heartbeats carry no spectrum; they are not plot data.
                if frame.role == "tx" or not frame.payload:
                    continue
                try:
                    spectrum = amplitude_spectrum(frame.payload, frame.first_word_invalid)
                except ValueError:
                    state.note_malformed()
                    continue
                record = frame_to_record(frame)
                record["sender_ip"] = addr[0]
                record["sender_port"] = addr[1]
                state.push_spectrum(
                    spectrum,
                    {
                        "sequence": record["sequence"],
                        "rssi_dbm": record["rssi_dbm"],
                        "channel": record["channel"],
                        "source_mac": record["source_mac"],
                        "firmware_drops_since_previous_frame": record[
                            "firmware_drops_since_previous_frame"
                        ],
                    },
                )
                if out_handle:
                    out_handle.write(json.dumps(record) + "\n")
                    out_handle.flush()
    except OSError as exc:
        print(f"CSI live plot receiver failed: {exc}", file=sys.stderr)
    finally:
        if out_handle:
            out_handle.close()


def replay_worker(state, path, rate_hz, loop, stop_event):
    """Offline test path: replay a JSONL capture through the same spectrum code."""
    import binascii

    interval = 1.0 / rate_hz if rate_hz > 0 else 0.0
    while not stop_event.is_set():
        try:
            handle = Path(path).open(encoding="utf-8")
        except OSError as exc:
            print(f"Cannot open replay file: {exc}", file=sys.stderr)
            return
        with handle:
            for line in handle:
                if stop_event.is_set():
                    return
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    payload = bytes.fromhex(rec.get("csi_payload_hex", rec.get("payload_hex", "")))
                    spectrum = amplitude_spectrum(
                        payload, rec.get("first_word_invalid", False)
                    )
                except (ValueError, binascii.Error):
                    state.note_malformed()
                    continue
                state.push_spectrum(
                    spectrum,
                    {
                        "sequence": rec.get("sequence", 0),
                        "rssi_dbm": rec.get("rssi_dbm", 0),
                        "channel": rec.get("channel", 0),
                        "source_mac": rec.get("source_mac", "?"),
                        "firmware_drops_since_previous_frame": rec.get(
                            "firmware_drops_since_previous_frame", 0
                        ),
                    },
                )
                if interval:
                    time.sleep(interval)
        if not loop:
            break


def run_plot(state, window, interval_ms, chip_info="", vmax=None,
             diff_mode=False, detect_mode=False, detect_params=None,
             z_span=60.0):
    import time as _time

    import matplotlib

    # Defer backend choice to the environment (PyQt5 if available).
    import matplotlib.pyplot as plt
    import numpy as np
    from collections import deque
    from matplotlib.animation import FuncAnimation

    if detect_mode:
        fig, (ax_spec, ax_water, ax_z) = plt.subplots(
            3, 1, figsize=(10, 9),
            gridspec_kw={"height_ratios": [1, 1.4, 0.7]})
        title = "CSI live waveform (amplitude + live detection)"
    else:
        fig, (ax_spec, ax_water) = plt.subplots(
            2, 1, figsize=(10, 7), gridspec_kw={"height_ratios": [1, 1.4]}
        )
        title = "CSI live waveform (amplitude)"
    if diff_mode:
        title += " (Δ vs window baseline)"
    fig.suptitle(title)
    (line,) = ax_spec.plot([], [], lw=1.2)
    ax_spec.set_xlabel("subcarrier index")
    ax_spec.set_ylabel("amplitude")
    ax_spec.grid(True, alpha=0.3)
    status_text = ax_spec.text(
        0.01, 0.95, "waiting for frames...", transform=ax_spec.transAxes,
        va="top", ha="left", fontsize=9,
        bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.6),
    )
    water_img = ax_water.imshow(
        np.zeros((1, 1)), aspect="auto", origin="lower", interpolation="nearest",
        cmap="RdBu_r" if diff_mode else "viridis",
    )
    ax_water.set_xlabel("subcarrier index")
    ax_water.set_ylabel(f"recent frames (window={window})")
    fig.colorbar(water_img, ax=ax_water,
                 label="\u0394 amplitude vs baseline" if diff_mode else "amplitude")
    live_keys = ("base_s", "short_s", "z_enter", "z_exit",
                 "min_dur_s", "warmup_s")
    detector = (LiveDetector(**{k: detect_params[k] for k in live_keys})
                if detect_mode and detect_params else None)
    z_hist = deque(maxlen=600)
    t_hist = deque(maxlen=600)
    fed_total = 0
    det_state = {"live": False}
    if detect_mode:
        (zline,) = ax_z.plot([], [], lw=1.0, color="tab:red")
        ax_z.axhline(detector.z_enter, color="r", ls="--", lw=1,
                    label="enter")
        ax_z.axhline(detector.z_exit, color="r", ls=":", lw=1, label="exit")
        ax_z.legend(fontsize=8, loc="upper right")
        ax_z.set_ylabel("z")
        ax_z.set_xlabel("seconds")
        ax_z.grid(True, alpha=0.3)
        banner = ax_z.text(0.01, 0.90, "quiet", transform=ax_z.transAxes,
                           va="top", ha="left", fontsize=11,
                           fontweight="bold",
                           bbox=dict(boxstyle="round", facecolor="lightgreen",
                                     alpha=0.7))
    fig.tight_layout()

    def update(_frame_no):
        nonlocal fed_total
        artists = [line, water_img, status_text]
        spectra, meta, stats = state.snapshot()
        if not spectra:
            return artists
        if detect_mode:
            tick = _time.monotonic()
            fresh, fed_total = new_spectra_since(
                spectra, stats["frames"], fed_total)
            for spec in fresh:
                _, live = detector.update(spec, tick)
                det_state["live"] = live
            z_hist.append(detector.z)
            t_hist.append(tick)
            t0 = t_hist[0]
            zline.set_data([t - t0 for t in t_hist], list(z_hist))
            ax_z.relim()
            ax_z.autoscale_view()
            ax_z.set_xlim(*rolling_xlim(tick - t_hist[0], z_span))
            banner.set_text("ACTIVE" if det_state["live"] else "quiet")
            banner.set_bbox(dict(boxstyle="round",
                                 facecolor="tomato" if det_state["live"]
                                 else "lightgreen", alpha=0.7))
            artists = artists + [zline, banner]
        latest = spectra[-1]
        xs = list(range(len(latest)))
        line.set_data(xs, latest)
        ax_spec.relim()
        ax_spec.autoscale_view()
        arr = np.array(spectra, dtype=float)
        display, lo, hi = waterfall_view(arr, vmax=vmax, diff_mode=diff_mode)[:3]
        water_img.set_data(display)
        water_img.set_clim(lo, hi)
        ax_water.set_ylim(-0.5, max(len(spectra) - 0.5, 0.5))
        ax_water.set_xlim(-0.5, len(latest) - 0.5)
        if vmax is None:
            scale_info = "scale=\u00b1auto" if diff_mode else "scale=auto"
        else:
            scale_info = (f"scale=\u00b1{vmax:g} fixed"
                          if diff_mode else f"scale=0-{vmax:g} fixed")
        if diff_mode:
            scale_info += " \u0394baseline"
        status_text.set_text(
            f"seq={meta.get('sequence', '?')}  "
            f"rssi={meta.get('rssi_dbm', '?')}dBm  "
            f"ch={meta.get('channel', '?')}  "
            f"mac={meta.get('source_mac', '?')}{chip_info}\n"
            f"fps={stats['fps']:.1f}  frames={stats['frames']}  "
            f"gaps={stats['gaps']}  fw_drops={stats['fw_drops']}  "
            f"malformed={stats['malformed']}  {scale_info}"
        )
        if detect_mode:
            status_text.set_text(status_text.get_text() +
                ("  DET:ACTIVE" if det_state["live"] else "  det:quiet"))
        return artists

    anim = FuncAnimation(fig, update, interval=interval_ms, blit=False, cache_frame_data=False)
    # Keep a reference so the animation is not garbage-collected.
    fig._csi_anim = anim
    plt.show()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("-p", "--port", type=int, default=5005)
    parser.add_argument("--chip", type=int, default=1)
    parser.add_argument("--window", type=int, default=100,
                        help="waterfall depth in frames (default: 100)")
    parser.add_argument("--vmax", type=float, default=None,
                        help="fixed waterfall color scale: 0..vmax, or +-vmax "
                             "with --diff (default: per-frame auto scale)")
    parser.add_argument("--detect", action="store_true",
                        help="third panel: live adaptive detection "
                             "z-score with ACTIVE banner (baseline is "
                             "the rolling window; use --window 300+ "
                             "for a slower baseline)")
    parser.add_argument("--detect-config", type=Path, default=None,
                        help="JSON threshold file for --detect (default: "
                             "shipped tools/csi_detect.json when present)")
    parser.add_argument("--zspan", type=float, default=60.0,
                        help="live z panel shows the last N seconds "
                             "(rolling window, default: 60)")
    parser.add_argument("--diff", action="store_true",
                        help="waterfall shows deviation from the window "
                             "baseline instead of raw amplitude")
    parser.add_argument("--interval", type=int, default=100,
                        help="GUI refresh interval in ms (default: 100)")
    parser.add_argument("--replay", type=Path, default=None,
                        help="replay a JSONL capture instead of UDP (offline test)")
    parser.add_argument("--replay-rate", type=float, default=11.0,
                        help="replay speed in frames/s (default: 11)")
    parser.add_argument("--loop", action="store_true",
                        help="loop the replay file")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="also archive live UDP frames to JSONL")
    args = parser.parse_args(argv)

    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.window < 2:
        parser.error("--window must be >= 2")
    if args.vmax is not None and args.vmax <= 0:
        parser.error("--vmax must be positive")
    if args.zspan <= 0:
        parser.error("--zspan must be positive")
    detect_params = None
    if args.detect:
        try:
            detect_params = resolve_params({}, args.detect_config)
        except ValueError as error:
            parser.error(str(error))
        print(f"Live detection thresholds: {detect_params}")

    state = LiveState(window=args.window)
    stop_event = threading.Event()

    if args.replay:
        print(f"Replaying {args.replay} at ~{args.replay_rate} frames/s")
        worker = threading.Thread(
            target=replay_worker,
            args=(state, args.replay, args.replay_rate, args.loop, stop_event),
            daemon=True,
        )
    else:
        print(f"Listening for CSI1 UDP frames on {args.listen}:{args.port}")
        print("Start this plotter BEFORE booting/resetting the ESP32.")
        if args.output:
            print(f"Also archiving to {args.output}")
        print("Close the plot window or press Ctrl-C to stop.")
        worker = threading.Thread(
            target=udp_worker,
            args=(state, args.listen, args.port, args.chip, stop_event, args.output),
            daemon=True,
        )
    worker.start()
    try:
        run_plot(state, args.window, args.interval, chip_info=f"  chip={args.chip}",
                 vmax=args.vmax, diff_mode=args.diff, detect_mode=args.detect,
                 detect_params=detect_params, z_span=args.zspan)
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        stop_event.set()
        worker.join(timeout=1.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
