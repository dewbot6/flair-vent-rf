"""Coarse frequency sweep across the FCC-confirmed 905-925 MHz band to localize
the real carrier, independent of modulation/datarate guesses. RSSI reflects
raw channel energy after the IF filter regardless of whether our demod
parameters are right, so this is a more robust way to find the true center
frequency than continuing to guess at 915.0 MHz exactly.

Toggle the vent open/close repeatedly and continuously while this runs --
each full band pass takes a few seconds, and it does several passes so any
toggle you make has a good chance of landing during a pass.

Run with: python3 freq_sweep.py
"""
from rflib import *
import time
import capture as c

START_MHZ = 905.0
END_MHZ = 925.0
STEP_MHZ = 0.25
DWELL_READS = 15          # RSSI samples per frequency step
TOTAL_SECONDS = 90        # hard cap, self-terminating


def sweep_once(d, results):
    freq = START_MHZ
    while freq <= END_MHZ:
        d.setFreq(int(freq * 1_000_000))
        best = -150
        for _ in range(DWELL_READS):
            rssi = c.rssi_dbm(d.getRSSI())
            if rssi > best:
                best = rssi
        key = round(freq, 2)
        results[key] = max(results.get(key, -150), best)
        freq += STEP_MHZ


if __name__ == "__main__":
    d = RfCat()
    print("ping:", d.ping())
    d.setModeIDLE()
    d.setMdmModulation(MOD_ASK_OOK)
    d.setMdmChanBW(150000)  # moderate width for coarse localization
    d.setModeRX()
    print(f"Sweeping {START_MHZ}-{END_MHZ} MHz in {STEP_MHZ}MHz steps, {TOTAL_SECONDS}s cap. Toggle now.")

    results = {}
    start = time.time()
    passes = 0
    while time.time() - start < TOTAL_SECONDS:
        sweep_once(d, results)
        passes += 1
        print(f"  ...pass {passes} done, {time.time()-start:.1f}s elapsed", flush=True)

    print(f"\nCompleted {passes} passes.")
    floor = min(results.values())
    print(f"Floor (min observed RSSI): {floor:.1f} dBm\n")
    ranked = sorted(results.items(), key=lambda kv: kv[1], reverse=True)
    print("Top 15 frequencies by peak RSSI:")
    for freq, rssi in ranked[:15]:
        bar = "#" * max(0, int(rssi - floor))
        print(f"  {freq:7.2f} MHz  {rssi:6.1f} dBm  {bar}")
