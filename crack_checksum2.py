"""Exhaustive checksum search: for a given frame length, try EVERY possible
start:end byte range (not just guesses) against every common CRC/checksum
algorithm, grouped by frame length so ranges are directly comparable.
"""
import re
import sys
from collections import defaultdict
import crcmod.predefined as crcmod_predefined

FRAME_RE = re.compile(r"FRAME \(len=(\d+)\): ([0-9A-F ]+)")

CRC_NAMES = [
    "crc-16", "crc-16-buypass", "crc-16-dds-110", "crc-16-dect",
    "crc-16-dnp", "crc-16-en-13757", "crc-16-genibus", "kermit",
    "crc-ccitt-false", "crc-aug-ccitt", "crc-16-maxim", "crc-16-mcrf4xx",
    "crc-16-riello", "crc-16-t10-dif", "crc-16-teledisk", "crc-16-usb",
    "x-25", "xmodem", "modbus", "crc-16-tms37157", "crc-16-a",
]
CRC_FUNCS = {}
for name in CRC_NAMES:
    try:
        CRC_FUNCS[name] = crcmod_predefined.mkCrcFun(name)
    except Exception:
        pass


def load_frames(path):
    frames = []
    with open(path) as f:
        for line in f:
            m = FRAME_RE.search(line)
            if m:
                length = int(m.group(1))
                data = bytes.fromhex(m.group(2).replace(" ", ""))
                if len(data) == length:
                    frames.append(data)
    return list({f.hex(): f for f in frames}.values())


def sum8(d): return bytes([sum(d) & 0xFF])
def sum16le(d): return (sum(d) & 0xFFFF).to_bytes(2, "little")
def sum16be(d): return (sum(d) & 0xFFFF).to_bytes(2, "big")
def xor8(d):
    x = 0
    for b in d:
        x ^= b
    return bytes([x])
def fletcher16(d):
    lo = hi = 0
    for b in d:
        lo = (lo + b) % 255
        hi = (hi + lo) % 255
    return bytes([lo, hi])


SIMPLE_FUNCS = {"sum8": sum8, "sum16le": sum16le, "sum16be": sum16be,
                "xor8": xor8, "fletcher16": fletcher16}


def exhaustive_search(frames):
    by_len = defaultdict(list)
    for f in frames:
        by_len[len(f)].append(f)

    for length, group in sorted(by_len.items()):
        if length < 4:
            continue
        print(f"\n=== Frame length {length} ({len(group)} unique) ===")
        found = False
        # trailing checksum could be 1 or 2 bytes
        for cksum_len in (2, 1):
            expected_all = [f[-cksum_len:] for f in group]
            for start in range(0, length - cksum_len):
                for end in range(start + 1, length - cksum_len + 1):
                    regions = [f[start:end] for f in group]
                    # try CRCs
                    for name, fn in CRC_FUNCS.items():
                        if cksum_len == 1:
                            continue
                        ok = True
                        for region, expected in zip(regions, expected_all):
                            val = fn(region)
                            if val.to_bytes(2, "little") != expected and val.to_bytes(2, "big") != expected:
                                ok = False
                                break
                        if ok:
                            print(f"*** MATCH crc={name} bytes[{start}:{end}] cksum_len={cksum_len} ***")
                            found = True
                    # try simple funcs
                    for name, fn in SIMPLE_FUNCS.items():
                        want_len = 1 if name in ("sum8", "xor8") else 2
                        if want_len != cksum_len:
                            continue
                        ok = True
                        for region, expected in zip(regions, expected_all):
                            if fn(region) != expected:
                                ok = False
                                break
                        if ok:
                            print(f"*** MATCH simple={name} bytes[{start}:{end}] cksum_len={cksum_len} ***")
                            found = True
        if not found:
            print("  no match found for this frame length")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "uart_monitor_poweron_sequence.log"
    frames = load_frames(path)
    print(f"Loaded {len(frames)} unique frames total.")
    exhaustive_search(frames)
