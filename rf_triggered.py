"""Capture the vent's over-the-air RF while triggering it on demand over UART.

Every previous RF attempt captured blind: gate on RSSI and hope the burst was
real. That failed because a whitened payload is statistically indistinguishable
from noise, so there was no way to tell a genuine packet from a false trigger.

Now that the CC430 can be commanded directly (see cc430_drive.py), the problem
changes shape. We know exactly WHEN a transmission happens and exactly WHAT it
carries, and we can repeat it. Noise does not repeat; a real packet does. So
the analysis is no longer "does this look like data" but "what appears in every
trial" -- which is a question with an answer.

Sends alternating positions so the captures also differ in a known way: bytes
common to all trials are framing, bytes that track the commanded value are
payload.

Run with:  python3 rf_triggered.py <serial_port> [--trials 6]
"""
import argparse
import json
import time
from collections import Counter

import serial
from rflib import RfCat, MOD_2FSK, ChipconUsbTimeoutException

import capture as cap
import cc430_drive as cd

# FCC-derived config: the filing gives GFSK at 915.0MHz with 93-96kHz occupied
# bandwidth, which matches a stock TI reference preset. See CLAUDE.md.
FREQ_MHZ = 915.0
DRATE = 38400
DEVIATION = 20000
CHAN_BW = 94000

# Real vent/puck traffic measures -40 to -50 dBm; the noise floor is near -96.
TRIGGER_DBM = -55


def capture_window(d, seconds, max_chunks=40):
    """Poll RSSI for `seconds`; pull RFrecv whenever the signal is strong.

    Bounded on BOTH chunk count and wall time -- rflib will happily firehose
    noise-floor bytes forever with sync-word detection off, and an unbounded
    loop here has previously been bad enough to outrun Ctrl-C.
    """
    chunks = []
    end = time.time() + seconds
    while time.time() < end and len(chunks) < max_chunks:
        rssi = cap.rssi_dbm(d.getRSSI())
        if rssi > TRIGGER_DBM:
            try:
                data, _ = d.RFrecv(timeout=100)
                chunks.append((time.time(), rssi, data))
            except ChipconUsbTimeoutException:
                pass
    return chunks


def common_runs(trials, minlen=6):
    """Byte sequences of >= minlen present in every trial.

    This is the whole point: a run appearing in all trials is structure
    (preamble, sync word, header), because uncorrelated noise will not repeat
    a 6-byte sequence across independent captures.
    """
    if not trials:
        return []
    def runs(b, n):
        return {bytes(b[i:i + n]) for i in range(len(b) - n + 1)}
    common = runs(trials[0], minlen)
    for t in trials[1:]:
        common &= runs(t, minlen)
        if not common:
            return []
    # keep only maximal runs (drop those contained in a longer common one)
    out = []
    for r in sorted(common, key=len, reverse=True):
        if not any(r in o for o in out):
            out.append(r)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("port")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--trials", type=int, default=6)
    p.add_argument("--window", type=float, default=9.0, help="seconds to capture after each command")
    p.add_argument("--out", default="rf_triggered_capture.json")
    args = p.parse_args()

    ser = serial.Serial(args.port, args.baud, timeout=0.2)
    print(f"UART open on {args.port}")

    d = RfCat()
    print("YARD Stick One ping:", d.ping()[0], "bytes ok")
    cap.configure(d, f"FCC-derived {DRATE}bps 2FSK dev={DEVIATION} bw={CHAN_BW} @ {FREQ_MHZ}MHz",
                  MOD_2FSK, DRATE, DEVIATION, CHAN_BW, FREQ_MHZ)

    baseline = cap.rssi_dbm(d.getRSSI())
    print(f"baseline RSSI now: {baseline:.1f} dBm (trigger at {TRIGGER_DBM})\n")

    results = []
    try:
        for i in range(args.trials):
            pos = 100 if i % 2 == 0 else 0
            frame = cd.build_command(pos)
            ser.reset_input_buffer()
            ser.write(frame)
            ser.flush()
            t0 = time.time()
            chunks = capture_window(d, args.window)
            total = sum(len(c[2]) for c in chunks)
            peak = max((c[1] for c in chunks), default=None)
            print(f"trial {i+1}/{args.trials}  pos={pos:3d}  "
                  f"{len(chunks):2d} bursts, {total:5d} bytes, "
                  f"peak {peak if peak is None else f'{peak:.1f} dBm'}")
            results.append({
                "trial": i, "position": pos, "t0": t0,
                "chunks": [{"t": t - t0, "rssi": r, "hex": data.hex()} for t, r, data in chunks],
            })
    finally:
        d.setModeIDLE()
        ser.close()

    with open(args.out, "w") as f:
        json.dump(results, f, indent=1)
    print(f"\nwrote {args.out}")

    joined = [b"".join(bytes.fromhex(c["hex"]) for c in r["chunks"]) for r in results]
    joined = [j for j in joined if j]
    print(f"\ntrials with data: {len(joined)}/{len(results)}")
    if len(joined) < 2:
        print("Not enough captured to compare. If bursts are 0, the CC430 may not be "
              "transmitting on command, or the PHY config is wrong.")
        return
    for n in (10, 8, 6):
        runs = common_runs(joined, n)
        if runs:
            print(f"\nbyte runs of >={n} present in ALL {len(joined)} trials:")
            for r in runs[:10]:
                print(f"   {r.hex(' ')}")
            break
    else:
        print("\nNo byte run of >=6 common to all trials -- consistent with the "
              "captures being noise rather than a demodulated packet.")


if __name__ == "__main__":
    main()
