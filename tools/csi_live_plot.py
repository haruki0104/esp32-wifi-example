#!/usr/bin/env python3
"""Live CSI waveform plot from the ESP32 UDP stream.

Subscribes to the same CSI1 UDP datagrams as tools/csi_udp_read.py and draws,
in real time:

  1. per-frame spectrum line: x = subcarrier index, y = amplitude
     (amplitude = sqrt(real^2 + imag^2), payload order is imag byte then
     real byte, signed int8; first 4 bytes skipped when first_word_invalid)
  2. scrolling waterfall: x = subcarrier index, y = recent frame sequence,
     color = amplitude

Network receive runs in a background thread with a bounded deque, so a slow
GUI never blocks the UDP socket. Optional --output archives the same JSONL
records as tools/csi_udp_read.py while plotting.

Examples:
  python3 tools/csi_live_plot.py --port 5005
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


def udp_worker(state, listen, port, chip, stop_event, output_file=None):
    """Background thread: recv CSI1 datagrams, push spectra, optionally archive."""
    out_handle = None
    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        out_handle = output_file.open("a", encoding="utf-8")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            from csi_read import HEADER, MAX_LEN

            sock.bind((listen, port))
            sock.settimeout(0.25)
            while not stop_event.is_set():
                try:
                    datagram, addr = sock.recvfrom(HEADER.size + MAX_LEN + 1)
                except socket.timeout:
                    continue
                try:
                    frame = parse_frame_datagram(datagram, chip)
                except ValueError:
                    state.note_malformed()
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


def run_plot(state, window, interval_ms, chip_info=""):
    import matplotlib

    # Defer backend choice to the environment (PyQt5 if available).
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.animation import FuncAnimation

    fig, (ax_spec, ax_water) = plt.subplots(
        2, 1, figsize=(10, 7), gridspec_kw={"height_ratios": [1, 1.4]}
    )
    fig.suptitle("CSI live waveform (amplitude)")
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
        np.zeros((1, 1)), aspect="auto", origin="lower", interpolation="nearest"
    )
    ax_water.set_xlabel("subcarrier index")
    ax_water.set_ylabel(f"recent frames (window={window})")
    fig.colorbar(water_img, ax=ax_water, label="amplitude")
    fig.tight_layout()

    def update(_frame_no):
        spectra, meta, stats = state.snapshot()
        if not spectra:
            return line, water_img, status_text
        latest = spectra[-1]
        xs = list(range(len(latest)))
        line.set_data(xs, latest)
        ax_spec.relim()
        ax_spec.autoscale_view()
        arr = np.array(spectra, dtype=float)
        water_img.set_data(arr)
        # Keep color scale stable-ish but adaptive: 1st/99th percentile.
        lo, hi = float(np.min(arr)), float(np.percentile(arr, 99))
        if hi <= lo:
            hi = lo + 1.0
        water_img.set_clim(lo, hi)
        ax_water.set_ylim(-0.5, max(len(spectra) - 0.5, 0.5))
        ax_water.set_xlim(-0.5, len(latest) - 0.5)
        status_text.set_text(
            f"seq={meta.get('sequence', '?')}  "
            f"rssi={meta.get('rssi_dbm', '?')}dBm  "
            f"ch={meta.get('channel', '?')}  "
            f"mac={meta.get('source_mac', '?')}{chip_info}\n"
            f"fps={stats['fps']:.1f}  frames={stats['frames']}  "
            f"gaps={stats['gaps']}  fw_drops={stats['fw_drops']}  "
            f"malformed={stats['malformed']}"
        )
        return line, water_img, status_text

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
        run_plot(state, args.window, args.interval, chip_info=f"  chip={args.chip}")
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        stop_event.set()
        worker.join(timeout=1.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
