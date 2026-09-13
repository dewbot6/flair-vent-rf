"""Test the DASH7 hypothesis directly using HARDWARE sync-word detection
instead of RSSI-threshold software gating + statistical guessing.

DASH7 spec defines standardized 16-bit PHY sync words (unwhitened, unlike
the payload): Sync Word Class 0 (background frames) = 0xE6D0, Class 1
(foreground/command frames) = 0x0B67. If this vent really speaks DASH7, the
CC1111's own hardware sync correlator can lock onto one of these directly --
no more guessing byte alignment, no more whitening confusion. A hit here
means RFrecv only returns when a real sync match occurs, and the returned
bytes are the properly-aligned post-sync payload, which finally makes
cross-capture comparison valid.

Run with: python3 sync_capture.py <sync_word_hex>
e.g. python3 sync_capture.py 0x0B67   (Class 1, foreground/command frames)
     python3 sync_capture.py 0xE6D0   (Class 0, background frames)
"""
from rflib import *
import sys
import time
import capture as c

MAX_SECONDS = 180


def main(sync_word):
    d = RfCat()
    print("ping:", d.ping())
    c.configure(d, "FCC-derived: 38.4kbps GFSK, 20kHz dev, 94kHz BW @ 915.0MHz",
                MOD_2FSK, 38400, 20000, 94000, 915.0)
    d.setModeIDLE()
    d.setMdmSyncWord(sync_word)
    d.setMdmSyncMode(SYNCM_15_of_16)
    d.setModeRX()
    print(f"Sync word set to 0x{sync_word:04X}, mode=15_of_16. Listening for {MAX_SECONDS}s. Toggle now.", flush=True)

    start = time.time()
    hits = 0
    while time.time() - start < MAX_SECONDS:
        try:
            data, ts = d.RFrecv(timeout=500)
            hits += 1
            print(f"[{time.time()-start:6.1f}s] HIT #{hits}: {len(data)} bytes  {data.hex()}", flush=True)
        except ChipconUsbTimeoutException:
            pass
    print(f"\nDone. {hits} sync-qualified hits in {MAX_SECONDS}s.")


if __name__ == "__main__":
    sync_word = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x0B67
    main(sync_word)
