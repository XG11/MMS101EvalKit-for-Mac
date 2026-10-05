#!/usr/bin/env python3
"""Real-time plot of MMS101 force/torque data.

Needs mms101_read.py in the same folder.
Requires: pip install pyserial matplotlib

Keys (plot window focused):  t = tare (zero all axes),  r = remove tare
Close the window to stop.
"""
import argparse
import collections
import csv
import sys
import threading
import time

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

from mms101_read import MMS101, MMS101Error, AXES, find_port

COLORS = ["tab:red", "tab:blue", "tab:green"]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial port (default: auto-detect)")
    ap.add_argument("--interval", type=int, default=1000,
                    help="sample interval in usec (default 1000)")
    ap.add_argument("--restart", type=int, default=100,
                    help="temperature update every N samples (default 100)")
    ap.add_argument("--window", type=float, default=5.0,
                    help="visible time span in seconds (default 5)")
    ap.add_argument("--csv", help="also log every sample to this CSV file")
    ap.add_argument("--cr", action="store_true",
                    help="append CR to each command (try if no response)")
    args = ap.parse_args()

    dev = MMS101(args.port or find_port(), append_cr=args.cr)
    out = None
    stop = threading.Event()
    reader = None
    try:
        # ---- sensor setup (same sequence as mms101_read.py) ----
        dev.stop()
        dev.board_select()
        print("Firmware:", dev.firmware_version(), file=sys.stderr)
        dev.power(True)
        time.sleep(0.1)
        dev.idle_all_axes()
        dev.bootload()
        dev.set_interval_us(args.interval)
        dev.set_restart(args.restart)
        dev.start()

        writer = None
        if args.csv:
            out = open(args.csv, "w", newline="")
            writer = csv.writer(out)
            writer.writerow(["t_s"] + AXES)

        # ---- shared state ----
        rate = 1e6 / max(args.interval, 500)
        buf = collections.deque(maxlen=int(args.window * rate) + 200)
        lock = threading.Lock()
        offset = np.zeros(6)
        error = []

        def read_loop():
            t_us = 0
            try:
                for dt, vals in dev.frames():
                    if stop.is_set():
                        break
                    t_us += dt
                    if t_us < 10_000:
                        continue  # first 10 ms is invalid per spec
                    t = t_us / 1e6
                    with lock:
                        buf.append((t, *vals))
                        off = offset.copy()
                    if writer:
                        writer.writerow([f"{t:.6f}"]
                                        + [f"{v - o:.5f}" for v, o in zip(vals, off)])
            except Exception as e:  # serial unplugged etc.
                if not stop.is_set():
                    error.append(e)

        reader = threading.Thread(target=read_loop, daemon=True)
        reader.start()

        # ---- figure ----
        fig, (ax_f, ax_m) = plt.subplots(2, 1, sharex=True, figsize=(10, 7))
        fig.canvas.manager.set_window_title("MMS101 live")
        lines = []
        for ax, names, unit in ((ax_f, AXES[:3], "Force [N]"),
                                (ax_m, AXES[3:], "Moment [N·m]")):
            for name, color in zip(names, COLORS):
                (ln,) = ax.plot([], [], color=color, lw=1, label=name)
                lines.append(ln)
            ax.set_ylabel(unit)
            ax.grid(True, alpha=0.3)
            ax.legend(loc="upper left", ncol=3)
        ax_m.set_xlabel("Time [s]   (t = tare, r = remove tare)")

        def on_key(event):
            nonlocal offset
            with lock:
                if event.key == "t" and len(buf) >= 10:
                    last = np.array(list(buf)[-10:])[:, 1:]
                    offset = last.mean(axis=0)
                elif event.key == "r":
                    offset = np.zeros(6)

        fig.canvas.mpl_connect("key_press_event", on_key)

        def update(_):
            if error:
                print("Reader error:", error[0], file=sys.stderr)
                plt.close(fig)
                return lines
            with lock:
                if len(buf) < 2:
                    return lines
                data = np.array(buf)
                off = offset.copy()
            t = data[:, 0]
            y = data[:, 1:] - off
            for i, ln in enumerate(lines):
                ln.set_data(t, y[:, i])
            ax_f.set_xlim(max(0.0, t[-1] - args.window), max(t[-1], args.window))
            for ax in (ax_f, ax_m):
                ax.relim()
                ax.autoscale_view(scalex=False)
            ax_f.set_title("  ".join(f"{n}={v:+.3f}" for n, v in zip(AXES[:3], y[-1, :3]))
                           + " N", fontsize=10, family="monospace")
            ax_m.set_title("  ".join(f"{n}={v:+.5f}" for n, v in zip(AXES[3:], y[-1, 3:]))
                           + " N·m", fontsize=10, family="monospace")
            return lines

        anim = FuncAnimation(fig, update, interval=33, cache_frame_data=False)  # noqa: F841
        fig.tight_layout()
        plt.show()
    except KeyboardInterrupt:
        pass
    except MMS101Error as e:
        print("Error:", e, file=sys.stderr)
    finally:
        stop.set()
        if reader:
            reader.join(timeout=1.0)
        try:
            dev.stop()
            dev.power(False)
        except Exception:
            pass
        dev.close()
        if out:
            out.close()


if __name__ == "__main__":
    main()