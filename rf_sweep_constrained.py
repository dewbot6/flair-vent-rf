"""Sweep the PHY configs consistent with the FCC's measured bandwidth.

Earlier sweeps in this project were effectively unconstrained, which is why
they were abandoned. But the FCC filing gives a *measured* occupied bandwidth
of 93-96 kHz across four vent models, and Carson's rule ties that to the two
unknowns:

    BW ~= 2 * (deviation + datarate/2)   =>   deviation + datarate/2 ~= 47 kHz

That is a curve, not a plane. It reduces the search to a handful of candidate
(datarate, deviation) pairs -- and notably excludes the 38.4k/20k config
tested previously, whose implied deviation should have been ~27.8 kHz.

Judging a hit: with no sync word the demodulator cannot byte-align, so
"readable bytes" is the wrong criterion. Payloads are also very likely
whitened. What survives both problems is the PREAMBLE -- an alternating
0xAA/0x55 run that is never whitened and shows up as strong period-1
structure. That is what this scores.

Run with:  python3 rf_sweep_constrained.py [--port /dev/cu.usbserial-XXXX]
Without --port it captures passively; with it, each config is tested against
UART-triggered transmissions.
"""
import argparse
import json
import time

from rflib import RfCat, MOD_2FSK, ChipconUsbTimeoutException

import capture as cap

TARGET_HALF_BW = 47000          # deviation + datarate/2, from the FCC bandwidth
CANDIDATE_RATES = [1200, 2400, 4800, 9600, 19200, 38400, 50000, 55555, 76800]
CHAN_BW = 94000
TRIGGER_DBM = -70               # below the -40..-50 of real traffic, above the floor


def candidates():
    out = []
    for rate in CANDIDATE_RATES:
        dev = TARGET_HALF_BW - rate / 2
        if dev < 1500:
            continue
        out.append((rate, int(dev)))
    return out


def capture_window(d, seconds, max_chunks=600):
    """Drain RF for a wall-clock window.

    max_chunks is deliberately high: rflib buffers in the dongle, so RFrecv
    returns a backlog immediately and a low cap ends the window in
    milliseconds while appearing to have run for seconds. Wall time is the
    real bound; the cap only exists so this can never run away.
    """
    chunks = []
    end = time.time() + seconds
    while time.time() < end and len(chunks) < max_chunks:
        if cap.rssi_dbm(d.getRSSI()) > TRIGGER_DBM:
            try:
                data, _ = d.RFrecv(timeout=100)
                chunks.append(data)
            except ChipconUsbTimeoutException:
                pass
    return b"".join(chunks)


def preamble_score(buf):
    """Longest run of alternating-bit bytes, plus period-1 autocorrelation.

    A real preamble is a long 0xAA/0x55 (or 0x0F/0xF0 at other alignments)
    sequence. Bit misalignment means it may not land on byte boundaries, so
    also report the best repeating-byte run of any value.
    """
    if len(buf) < 32:
        return 0, 0.0, None
    best_len, best_val, cur_len, cur_val = 0, None, 0, None
    for b in buf:
        if b == cur_val:
            cur_len += 1
            if cur_len > best_len:
                best_len, best_val = cur_len, cur_val
        else:
            cur_val, cur_len = b, 1
    n = min(4000, len(buf) - 1)
    ac = sum(1 for i in range(n) if buf[i] == buf[i + 1]) / n if n else 0.0
    return best_len, ac, best_val


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", help="UART port; if given, trigger transmissions per config")
    p.add_argument("--window", type=float, default=8.0)
    p.add_argument("--freq", type=float, default=915.0)
    p.add_argument("--out", default="rf_sweep_constrained.json")
    args = p.parse_args()

    ser = None
    if args.port:
        import serial
        import cc430_drive as cd
        ser = serial.Serial(args.port, 115200, timeout=0.2)
        build = cd.build_command
        print(f"UART trigger enabled on {args.port}")

    d = RfCat()
    print("dongle ping ok\n")
    results = []
    try:
        for i, (rate, dev) in enumerate(candidates()):
            cap.configure(d, f"{rate}bps dev={dev}", MOD_2FSK, rate, dev, CHAN_BW, args.freq)
            if ser:
                ser.write(build(100 if i % 2 == 0 else 0))
                ser.flush()
            buf = capture_window(d, args.window)
            runlen, ac, val = preamble_score(buf)
            flag = ""
            if runlen >= 6 or ac > 0.05:
                flag = "   <-- STRUCTURE"
            print(f"  {rate:6d}bps dev={dev:6d}: {len(buf):6d} bytes, "
                  f"longest run {runlen:3d}"
                  f"{'' if val is None else f' of 0x{val:02X}'}, "
                  f"adjacent-equal {ac:.4f}{flag}")
            results.append({"rate": rate, "dev": dev, "bytes": len(buf),
                            "run": runlen, "run_val": val, "adj": ac,
                            "hex": buf[:4000].hex()})
    finally:
        d.setModeIDLE()
        if ser:
            ser.close()

    with open(args.out, "w") as f:
        json.dump(results, f, indent=1)
    print(f"\nwrote {args.out}")
    print("\nbaseline for reference: uncorrelated noise gives adjacent-equal ~0.004")
    best = sorted(results, key=lambda r: (-r["run"], -r["adj"]))[:3]
    print("most structured configs:")
    for r in best:
        print(f"   {r['rate']}bps dev={r['dev']}: run={r['run']} adj={r['adj']:.4f}")


if __name__ == "__main__":
    main()
