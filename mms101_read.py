#!/usr/bin/env python3
"""Read 6-axis force/torque data from a Mitsumi MMS101 Evakit1 (ForceSensorControllerBoard).

Protocol per "Communication Specification of ForceSensorController" rev.5.
Requires: pip install pyserial
"""
import argparse
import csv
import glob
import sys
import time

import serial

AXES = ["Fx", "Fy", "Fz", "Mx", "My", "Mz"]

# Scaling per MMS101 datasheet rev4.1 ("Matrix operation" / theoretical
# resolution): 1 LSB = 0.001 N for forces, 0.00001 N*m for moments.
FORCE_SCALE = 1.0 / 1000.0      # raw -> N
MOMENT_SCALE = 1.0 / 100000.0   # raw -> Nm

STATUS = {
    0x01: "Illegal Command (bad timing)",
    0x03: "Illegal Command Parameter",
    0x08: "Sensor Access Error (check sensor/cable/power)",
    0x10: "Not Support Command",
}

FRAME_LEN = 25  # status, len(0x17), 0x80, 0x00, 6 x 3 bytes, 3 bytes time


class MMS101Error(RuntimeError):
    pass


class MMS101:
    def __init__(self, port, append_cr=False):
        self.append_cr = append_cr
        self.ser = serial.Serial(
            port, baudrate=1_000_000, bytesize=8, parity="N", stopbits=1,
            timeout=2.0, rtscts=False, xonxoff=False,
        )

    # ---- low level -------------------------------------------------------
    def _read_exact(self, n):
        data = self.ser.read(n)
        if len(data) != n:
            raise MMS101Error(f"timeout: wanted {n} bytes, got {len(data)}")
        return data

    def _send(self, payload, instruction=0x54):
        pkt = bytes([instruction, len(payload)]) + bytes(payload)
        if self.append_cr:
            pkt += b"\r"
        self.ser.write(pkt)

    def _cmd(self, name, payload, instruction=0x54):
        self._send(payload, instruction)
        status, n = self._read_exact(2)
        data = self._read_exact(n) if n else b""
        if status != 0x00:
            raise MMS101Error(
                f"{name}: status 0x{status:02X} {STATUS.get(status, 'unknown')}")
        return data

    # ---- commands --------------------------------------------------------
    def board_select(self):
        self._cmd("BoardSelect", [0x10, 0x00])

    def firmware_version(self):
        return ".".join(str(b) for b in self._cmd("FirmwareVersion", [0x15]))

    def power(self, on):
        # Only VDD12 (0x00) and VDD45 (0x05) may be switched on.
        for ldo in (0x00, 0x05):
            self._cmd("PowerSwitch", [0x36, ldo, 0x01 if on else 0x00])
            time.sleep(0.05)

    def idle_all_axes(self):
        for axis in range(6):
            self._cmd("AxisSelect", [0x1C, axis])
            # Idle uses a different instruction code (0x53) + SPI write (0x57)
            self._cmd("Idle", [0x57, 0x94], instruction=0x53)
        time.sleep(0.02)  # spec: >= 10 ms before Bootload

    def bootload(self):
        self._cmd("Bootload", [0xB0])

    def coefficients(self):
        rows = []
        for axis in range(6):
            row = []
            for c in range(6):
                d = self._cmd("Coefficient", [0x27, axis, c])
                row.append(int.from_bytes(d, "big", signed=True))
            rows.append(row)
        return rows

    def set_interval_us(self, usec):
        self._cmd("IntervalMeasure", [0x43, *usec.to_bytes(3, "big")])

    def set_restart(self, count):
        self._cmd("IntervalRestart", [0x44, *count.to_bytes(3, "big")])

    def start(self):
        self._cmd("Start", [0x23, 0x00])  # first response is status only

    def stop(self):
        """Stop streaming and drain whatever is still in the pipe."""
        self._send([0x33])
        time.sleep(0.1)
        self.ser.reset_input_buffer()

    def close(self):
        self.ser.close()

    # ---- streaming -------------------------------------------------------
    def frames(self):
        """Yield (dt_usec, [Fx, Fy, Fz, Mx, My, Mz]) in physical units."""
        buf = bytearray()
        while True:
            buf += self.ser.read(max(1, self.ser.in_waiting))
            while len(buf) >= FRAME_LEN:
                if not (buf[0] == 0x00 and buf[1] == 0x17
                        and buf[2] == 0x80 and buf[3] == 0x00):
                    del buf[0]  # resync
                    continue
                f = bytes(buf[:FRAME_LEN])
                del buf[:FRAME_LEN]
                raw = [int.from_bytes(f[4 + 3 * i: 7 + 3 * i], "big", signed=True)
                       for i in range(6)]
                vals = ([v * FORCE_SCALE for v in raw[:3]]
                        + [v * MOMENT_SCALE for v in raw[3:]])
                dt = int.from_bytes(f[22:25], "big")
                yield dt, vals


def find_port():
    ports = sorted(glob.glob("/dev/cu.usbserial*"))
    if not ports:
        sys.exit("No /dev/cu.usbserial* device found. Is the board plugged in "
                 "(data-capable USB-C cable)? Otherwise pass --port.")
    if len(ports) > 1:
        print(f"Multiple ports found {ports}; using {ports[0]}", file=sys.stderr)
    return ports[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", help="serial port (default: auto-detect)")
    ap.add_argument("--interval", type=int, default=1000,
                    help="sample interval in usec (default 1000)")
    ap.add_argument("--restart", type=int, default=100,
                    help="temperature update every N samples, 0 = once (default 100)")
    ap.add_argument("--tare", action="store_true",
                    help="subtract the average of the first 10 samples")
    ap.add_argument("--csv", help="write every sample to this CSV file")
    ap.add_argument("--coeffs", action="store_true",
                    help="print the 6x6 matrix correction coefficients")
    ap.add_argument("--cr", action="store_true",
                    help="append CR to each command (try if no response)")
    args = ap.parse_args()

    dev = MMS101(args.port or find_port(), append_cr=args.cr)
    out = None
    try:
        dev.stop()  # in case a previous run left it streaming
        dev.board_select()
        print("Firmware:", dev.firmware_version(), file=sys.stderr)
        dev.power(True)
        time.sleep(0.1)
        dev.idle_all_axes()
        dev.bootload()
        if args.coeffs:
            for name, row in zip(AXES, dev.coefficients()):
                print(name, row, file=sys.stderr)
        dev.set_interval_us(args.interval)
        dev.set_restart(args.restart)
        dev.start()

        writer = None
        if args.csv:
            out = open(args.csv, "w", newline="")
            writer = csv.writer(out)
            writer.writerow(["t_s"] + AXES)

        t_us = 0
        offset = [0.0] * 6
        tare_buf = []
        last_print = 0.0
        print("Streaming, Ctrl-C to stop.", file=sys.stderr)
        for dt, vals in dev.frames():
            t_us += dt
            if t_us < 10_000:
                continue  # spec: data in the first 10 ms is invalid
            if args.tare and len(tare_buf) < 10:
                tare_buf.append(vals)
                if len(tare_buf) == 10:
                    offset = [sum(c) / 10 for c in zip(*tare_buf)]
                continue
            vals = [v - o for v, o in zip(vals, offset)]
            if writer:
                writer.writerow([f"{t_us / 1e6:.6f}"] + [f"{v:.5f}" for v in vals])
            now = time.monotonic()
            if now - last_print >= 0.05:  # ~20 Hz console output
                last_print = now
                print(f"{t_us / 1e6:9.3f}s  "
                      + "  ".join(f"{n}={v:+8.3f}" for n, v in zip(AXES[:3], vals[:3]))
                      + " N   "
                      + "  ".join(f"{n}={v:+8.5f}" for n, v in zip(AXES[3:], vals[3:]))
                      + " Nm")
    except KeyboardInterrupt:
        pass
    except MMS101Error as e:
        print("Error:", e, file=sys.stderr)
    finally:
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