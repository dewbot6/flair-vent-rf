"""Sweep candidate PHY configs while the vent is repeatedly toggled, to find
which config (if any) produces real repeated-preamble packet structure
instead of noise. See CLAUDE.md for the CANDIDATE_CONFIGS hypothesis list.

Run with: python3 sweep_configs.py
Toggle the vent open/close continuously (every ~6-8s) for the whole sweep --
each config gets SECONDS_PER_CONFIG of window to catch a few toggles.
"""
from rflib import *
import capture as c

SECONDS_PER_CONFIG = 45

CENTER_MHZ = 906.5  # corrected from 915.0 -- freq_sweep.py found the real carrier
                     # clustered at 906.25-908.25 MHz, not 915.0

CANDIDATE_CONFIGS = [
    # Re-testing all 4, including Normal Rate -- prior noise verdicts were all
    # measured ~7-9MHz off the real carrier, so they don't actually rule anything out.
    ("D7A-like Normal Rate (55.555k GFSK)", MOD_2FSK,    55555,  50000, 100000, CENTER_MHZ),
    ("D7A-like Lo Rate (9.6k GFSK)",        MOD_2FSK,     9600,  25000,  62500, CENTER_MHZ),
    ("D7A-like Hi Rate (166.6k GFSK)",      MOD_2FSK,   166666,  75000, 200000, CENTER_MHZ),
    ("Generic OOK 4.8k",                    MOD_ASK_OOK,  4800,      0, 100000, CENTER_MHZ),
]


def slug(label):
    return "".join(ch if ch.isalnum() else "_" for ch in label).strip("_").lower()


if __name__ == "__main__":
    d = RfCat()
    print("ping:", d.ping())
    for label, mod, datarate, deviation, bw, freq_mhz in CANDIDATE_CONFIGS:
        c.configure(d, label, mod, datarate, deviation, bw, freq_mhz)
        print(f">>> Capturing '{label}' for {SECONDS_PER_CONFIG}s -- toggle the vent now <<<", flush=True)
        c.gated_capture(d, max_seconds=SECONDS_PER_CONFIG, note=f"sweep:{label}")
        fname = f"sweep_{slug(label)}.log"
        print(f"(this config's captures are in the combined stdout log; see analyze_sweep.py for per-config breakdown)")
    print("Sweep complete.")
