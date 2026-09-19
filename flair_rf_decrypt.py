"""Decrypt captured Flair RF packets once the XTEA key is known.

Turnkey for after the SBW key read: set KEY_BYTES in flair_xtea.py, then run
    python3 flair_rf_decrypt.py captures/dual_capture.json
It groups packets by type, brute-forces the per-session counter high byte
(only the low byte is on air), and prints the decrypted payloads for the high
byte that yields the most structured output (zero density). That pins both the
key and the counter, closing the loop.

Until the key is set this just explains what it will do.
"""
import json
import sys
from collections import defaultdict

import flair_xtea


def load_rf(path):
    out = []
    for e in json.load(open(path)):
        if isinstance(e, dict) and e.get("kind") == "rf":
            p = bytes.fromhex(e["hex"])
            if len(p) >= 21:
                out.append(p)
    return out


def best_counter_high(pkts, key):
    """Return the counter_high (0..255) giving the most zero bytes across pkts."""
    best_h, best_z = 0, -1
    for h in range(256):
        z = sum(flair_xtea.decrypt_packet(p, key, h).count(0) for p in pkts)
        if z > best_z:
            best_z, best_h = z, h
    return best_h, best_z


def main():
    if flair_xtea.KEY_BYTES is None:
        print("KEY_BYTES not set in flair_xtea.py.")
        print("Read it first:  mspdebug ezfet \"md 0x2830 0x10\"  -> 16 bytes -> KEY_BYTES")
        return
    if len(sys.argv) < 2:
        print("usage: flair_rf_decrypt.py <capture.json>")
        return
    pkts = load_rf(sys.argv[1])
    groups = defaultdict(list)
    for p in pkts:
        groups[(p[17], p[18])].append(p)
    for key, G in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        h, z = best_counter_high(G, flair_xtea.KEY_BYTES)
        tot = sum(len(p) - 20 for p in G)
        print(f"\ntype {key[0]:02x} {key[1]:02x}  n={len(G)}  counter_high=0x{h:02x}  "
              f"zeros={z}/{tot} (rand ~{tot/256:.1f})")
        for p in G[:6]:
            pt = flair_xtea.decrypt_packet(p, flair_xtea.KEY_BYTES, h)
            print(f"  ctr={p[19]:02x}  {pt.hex(' ')}")


if __name__ == "__main__":
    main()
