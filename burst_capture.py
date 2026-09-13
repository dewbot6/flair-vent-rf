"""Reconstruct a full continuous burst instead of disjoint 255-byte snapshots.

Rationale: real protocol payloads are almost always whitened (XORed with a
PRBS) so they look statistically random even when correctly demodulated --
only the preamble + sync word (first few bytes of a packet) stay unwhitened
and would show real periodicity. A single fixed 255-byte RFrecv might not
reliably land on that specific window. Instead, on an RSSI trigger, pull
consecutive RFrecv reads back-to-back (no artificial gap) to reconstruct one
long continuous buffer spanning the whole transmission, then scan that single
buffer for a local region of strong periodicity (the preamble) using a
sliding-window autocorrelation, distinguishing it from the flatter
(whitened) payload that should follow it.

Run with: python3 burst_capture.py
"""
from rflib import *
import time
import capture as c

MAX_CHUNKS_PER_BURST = 24     # cap: 24 * 255 = ~6120 bytes max per burst
INTER_READ_TIMEOUT_MS = 30    # short -- if FIFO is empty this fast, burst likely ended
MAX_SECONDS = 240


def capture_burst(d):
    """Called right after an RSSI trigger. Pulls consecutive RFrecv reads
    with no processing gap until the burst appears to end or the cap hits.

    IMPORTANT: with sync-word detection off, the FIFO always has *something*
    in it continuously (real signal or just noise-floor bits) -- RFrecv
    timing out is NOT a reliable "burst ended" signal by itself. Must
    re-check RSSI between reads and stop once the real signal actually
    drops, or this just pads real short packets with a long noise tail.
    """
    chunks = []
    for _ in range(MAX_CHUNKS_PER_BURST):
        rssi = c.rssi_dbm(d.getRSSI())
        if rssi <= c.THRESHOLD_DBM:
            break
        try:
            data, _ = d.RFrecv(timeout=INTER_READ_TIMEOUT_MS)
            chunks.append(data)
        except ChipconUsbTimeoutException:
            break
    return b"".join(chunks)


def find_periodic_region(buf, window=24, max_lag=8):
    """Slide a window across buf; for each position, find the lag (1..max_lag)
    with the highest byte-match rate in that window. Returns the list of
    (offset, best_lag, match_rate) so we can see where periodicity peaks --
    a real preamble region should stand out sharply above the rest."""
    results = []
    for offset in range(0, len(buf) - window - max_lag, window // 2):
        seg = buf[offset:offset + window + max_lag]
        best_lag, best_rate = 0, 0.0
        for lag in range(1, max_lag + 1):
            total = window
            matches = sum(1 for i in range(window) if seg[i] == seg[i + lag])
            rate = matches / total
            if rate > best_rate:
                best_rate, best_lag = rate, lag
        results.append((offset, best_lag, best_rate))
    return results


if __name__ == "__main__":
    d = RfCat()
    print("ping:", d.ping())
    c.configure(d, "FCC-derived: 38.4kbps GFSK, 20kHz dev, 94kHz BW @ 915.0MHz",
                MOD_2FSK, 38400, 20000, 94000, 915.0)

    start = time.time()
    burst_count = 0
    while time.time() - start < MAX_SECONDS:
        rssi = c.rssi_dbm(d.getRSSI())
        if rssi > c.THRESHOLD_DBM:
            burst = capture_burst(d)
            burst_count += 1
            print(f"\n=== Burst {burst_count}: {len(burst)} bytes, trigger RSSI {rssi:.1f} dBm ===", flush=True)
            print(burst.hex(), flush=True)
            regions = find_periodic_region(burst)
            strong = [r for r in regions if r[2] > 0.6]
            if strong:
                print(f"  Periodic regions found (>60% match at some lag): {strong}", flush=True)
            else:
                print("  No strongly periodic region found in this burst.", flush=True)
    print(f"\nDone. {burst_count} bursts captured in {time.time()-start:.1f}s.")
