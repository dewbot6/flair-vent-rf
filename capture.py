"""RSSI-gated capture script for the Flair vent RF RE project.

Run with: python3 capture.py
See CLAUDE.md for context, candidate PHY configs, and the RSSI conversion.
"""
from rflib import *
import time

FLOOR_DBM = -96
THRESHOLD_DBM = FLOOR_DBM + 15  # adjust once real traffic is observed


def rssi_dbm(raw_byte):
    val = raw_byte[0]
    if val >= 128:
        val -= 256
    return (val / 2) - 74


def configure(d, label, mod, datarate, deviation, bw, freq_mhz):
    d.setModeIDLE()
    d.setMdmModulation(mod)
    d.setMdmDRate(datarate)
    if mod == MOD_2FSK:
        d.setMdmDeviatn(deviation)
    # The CC1111 only supports 16 discrete channel bandwidths (a formula of
    # mantissa/exponent off the 24MHz crystal, floor ~53.6kHz). Bump up in
    # 5% steps until we land on a bw the radio can actually produce, instead
    # of crashing the whole sweep on one bad guess.
    actual_bw = bw
    for _ in range(50):
        try:
            d.setMdmChanBW(actual_bw)
            break
        except Exception:
            actual_bw *= 1.05
    else:
        raise Exception(f"Could not find a valid ChanBW near {bw}")
    if actual_bw != bw:
        print(f"  (requested ChanBW {bw}Hz not achievable, used {actual_bw:.0f}Hz instead)")
    d.setFreq(int(freq_mhz * 1_000_000))
    d.setMdmSyncMode(0)
    d.setEnableMdmManchester(False)
    d.setModeRX()
    print(f"Configured: {label}")


def gated_capture(d, max_iters=2_000_000, max_seconds=300, note="", threshold=THRESHOLD_DBM,
                   heartbeat_every=20000):
    """Runs until max_iters OR max_seconds, whichever comes first (both are hard caps,
    never an unbounded loop). Prints a heartbeat periodically so progress is visible
    even when stdout is piped through something buffered (e.g. `tee`)."""
    count = 0
    hits = 0
    start = time.time()
    while count < max_iters and (time.time() - start) < max_seconds:
        rssi = rssi_dbm(d.getRSSI())
        if rssi > threshold:
            ts = time.time()
            print(ts, note, "RSSI spike:", rssi, flush=True)
            try:
                data, _ = d.RFrecv(timeout=200)
                print("  ->", len(data), data.hex(), flush=True)
                hits += 1
            except ChipconUsbTimeoutException:
                pass
        count += 1
        if count % heartbeat_every == 0:
            print(f"  ...heartbeat: {count} iters, {time.time()-start:.1f}s elapsed, {hits} hits so far", flush=True)
    elapsed = time.time() - start
    print(f"Done. {hits} captures out of {count} iterations in {elapsed:.1f}s.", flush=True)
    return hits


if __name__ == "__main__":
    d = RfCat()
    print("ping:", d.ping())
    configure(d, "D7A-like Normal Rate (55.555k GFSK)", MOD_2FSK, 55555, 50000, 100000, 915.0)
    gated_capture(d, max_seconds=300, note="baseline silence check, puck off")
