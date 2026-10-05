# MMS101 Evakit1 reader for macOS

Python scripts for reading the Mitsumi MMS101 6-axis force/torque sensor through the MMS101 Evakit1 (ForceSensorControllerBoard) over USB, without the Windows evaluation application.

| File | Purpose |
|---|---|
| `mms101_read.py` | Streams readings to the terminal and optionally to a CSV file. Also provides the `MMS101` class used by the plotter. |
| `mms101_plot.py` | Live plot of forces and moments. Needs `mms101_read.py` in the same folder. |

Written against the Communication Specification of ForceSensorController rev.5 and the MMS101 datasheet rev.4.1 (model MMS101BXXA). The MMS101C09 is not covered by those documents and may need a different sequence.

## Hardware setup

1. Plug the sensor's flex cable into the conversion board.
2. Connect the conversion board to the evaluation board with the 20-pin lead cable.
3. Connect the evaluation board to the Mac with a USB-C data cable. The USB power LED should light.

## Installation

```bash
mkdir ~/mms101 && cd ~/mms101      # put both scripts here
python3 -m venv venv
source venv/bin/activate
pip install pyserial matplotlib
```

Check that the board is visible:

```bash
ls /dev/cu.usbserial-*
```

macOS includes an FTDI driver. If nothing is listed, try another cable, then try install the FTDI VCP driver.(as for Oct 5 2026 this website shown in Mitsumi datasheet is invalid, maybe try https://ftdichip.com/drivers/vcp-drivers/ instead)

## Usage

Activate the environment in each new terminal first: `source ~/mms101/venv/bin/activate`.

### Terminal output and logging

```bash
python mms101_read.py                        # stream to the terminal
python mms101_read.py --tare --csv log.csv   # zero at start, log every sample
```

Press Ctrl-C to stop.

### Live plot

```bash
python mms101_plot.py
python mms101_plot.py --window 10 --csv log.csv
```

With the plot window focused, press `t` to zero all axes (average of the last 10 samples) and `r` to remove the zero. Close the window to stop.

### Options

| Option | Scripts | Meaning | Default |
|---|---|---|---|
| `--port PATH` | both | Serial port | auto-detect `/dev/cu.usbserial*` |
| `--interval USEC` | both | Sample interval in microseconds | 1000 (1 kHz) |
| `--restart N` | both | Refresh temperature correction every N samples; 0 = only at start | 100 |
| `--csv FILE` | both | Log every sample to a CSV file | off |
| `--cr` | both | Append CR to each command; try if the board does not answer | off |
| `--tare` | read | Subtract the average of the first 10 samples | off |
| `--coeffs` | read | Print the 6×6 calibration matrix | off |
| `--window SEC` | plot | Visible time span | 5 |

## Output

- Forces Fx, Fy, Fz in newtons (1 count = 0.001 N).
- Moments Mx, My, Mz in newton-metres (1 count = 0.00001 N·m).
- CSV columns: `t_s, Fx, Fy, Fz, Mx, My, Mz`. Time comes from the board's own per-sample timer. Tare offsets, when active, are already subtracted.

To measure the actual sample rate from a log:

```bash
python -c "import csv; r=list(csv.reader(open('log.csv')))[1:]; print((len(r)-1)/(float(r[-1][0])-float(r[0][0])), 'Hz')"
```

## Things to keep in mind

**Load limits**

- Accurate range: ±40 N and ±0.4 N·m.
- Destruction limit: ±200 N and ±1.8 N·m. Moments reach this easily through a long lever (18 N at 10 cm).

**Accuracy**

- ±5% of full scale (±2 N, ±0.02 N·m); linearity and hysteresis ±1% of full scale each.
- Noise: about 0.04 N RMS (Fx, Fy), 0.06 N (Fz), 0.0004 N·m (Mx, My), 0.0008 N·m (Mz).
- The sensor updates every 781 µs, so intervals below 1000 µs give no extra information.

**Drift and zeroing**

- Fz drifts after start-up. Wait about 5 minutes before taring or recording.
- Re-tare after remounting, changing the attached tool, or changing orientation.
- Each temperature refresh takes about 7.5 ms, during which samples repeat the last value.
- Operating temperature: 5–45 °C.

**Mounting**

- M1.6 screws, entering the sensor no more than 1.7 mm.
- Tighten diagonally in stages to 0.15 N·m; never fully tighten one screw first.
- Mounting surfaces flat to 0.03 mm and rigid.

**Flex cable**

- Do not pull it while the sensor is screwed down, and do not bend it sharply at the stiffened connector end.

## Shutting down

1. Stop the script (Ctrl-C or close the plot window). It stops the measurement and switches off the sensor supplies.
2. Unplug the USB cable.
3. Only then disconnect the sensor or lead cable.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No /dev/cu.usbserial* device found` | Check the cable (must carry data) and port; install the FTDI driver. |
| `timeout: wanted 2 bytes, got 0` | Run with `--cr`; otherwise press the board's reset button and retry. |
| `Sensor Access Error` | Reseat the flex cable and the lead cable. |
| `Illegal Command (bad timing)` | Press the reset button or replug USB, then rerun. |
| `Resource busy` | Another program has the port open; close it. |
| `ModuleNotFoundError: mms101_read` | Run the plotter from the folder containing both scripts. |

## How it works

The board appears as a serial port at 1,000,000 baud, 8N1, no flow control. Commands are binary packets: `0x54, length, command ID, parameters`, answered by `status, length, data`. The start-up sequence is:

1. Board Select (`0x10`)
2. Power Switch (`0x36`) for the 1.2 V and 4.5 V sensor supplies
3. For each of the six axes: Axis Select (`0x1C`), then Idle (`0x53 0x02 0x57 0x94`)
4. Wait at least 10 ms, then Bootload (`0xB0`) to load the calibration matrix
5. Interval Measure (`0x43`) and Interval Restart (`0x44`)
6. Start (`0x23`)

After Start, the board sends a 25-byte frame per sample containing six signed 24-bit values (already calibrated by the board) and the microseconds elapsed since the previous sample. Stop (`0x33`) ends the stream. The first 10 ms of data are discarded as the spec requires.

## Reference documents

- MMS101 Evakit1 User's Guide, Ver.5.4
- Communication Specification of ForceSensorController, Rev.5
- MMS101 Data Sheet, Rev.4.1