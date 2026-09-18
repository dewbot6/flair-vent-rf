"""Systematically test common CRC-16/checksum algorithms against real captured
UART frames, to find which one (if any) matches the trailing 2 bytes of every
frame -- so we can compute valid checksums for our own forged frames instead
of only being able to replay old ones verbatim.

Tests each candidate algorithm against ALL captured frames, over several
candidate byte ranges (since we don't know for certain exactly which bytes
are covered), and reports any that match on every single frame.
"""
import re
import sys
import crcmod.predefined as crcmod_predefined

FRAME_RE = re.compile(r"FRAME \(len=(\d+)\): ([0-9A-F ]+)")


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
    # dedupe
    return list({f.hex(): f for f in frames}.values())


def simple_checksums(data):
    """A handful of simple, non-CRC checksum schemes to try too."""
    results = {}
    s8 = sum(data) & 0xFF
    results["sum8"] = bytes([s8])
    s16 = sum(data) & 0xFFFF
    results["sum16_le"] = s16.to_bytes(2, "little")
    results["sum16_be"] = s16.to_bytes(2, "big")
    x = 0
    for b in data:
        x ^= b
    results["xor8"] = bytes([x])
    # Fletcher-16
    lo, hi = 0, 0
    for b in data:
        lo = (lo + b) % 255
        hi = (hi + lo) % 255
    results["fletcher16_le"] = bytes([lo, hi])
    results["fletcher16_be"] = bytes([hi, lo])
    return results


CRC_NAMES = [
    "crc-16", "crc-16-buypass", "crc-16-dds-110", "crc-16-dect",
    "crc-16-dnp", "crc-16-en-13757", "crc-16-genibus", "kermit",
    "crc-ccitt-false", "crc-aug-ccitt", "crc-16-maxim", "crc-16-mcrf4xx",
    "crc-16-riello", "crc-16-t10-dif", "crc-16-teledisk", "crc-16-usb",
    "x-25", "xmodem", "modbus", "crc-16-tms37157", "crc-16-a",
]


def test_frames(frames, verbose_mismatch_limit=2):
    n = len(frames)
    print(f"Testing against {n} unique frames.\n")

    # candidate ranges: (name, slice applied to full frame bytes)
    ranges = [
        ("all-but-last-2", lambda d: d[:-2]),
        ("bytes[1:-2] (skip length byte)", lambda d: d[1:-2]),
        ("bytes[0:-2] with 0x7B prefix", lambda d: b"\x7b" + d[:-2]),
    ]

    found_any = False
    for rname, rfn in ranges:
        for crcname in CRC_NAMES:
            try:
                crc_fn = crcmod_predefined.mkCrcFun(crcname)
            except Exception:
                continue
            mismatches = 0
            for f in frames:
                region = rfn(f)
                expected = f[-2:]
                val = crc_fn(region)
                for byteorder in ("little", "big"):
                    computed = val.to_bytes(2, byteorder)
                    if computed == expected:
                        break
                else:
                    mismatches += 1
            if mismatches == 0:
                print(f"*** MATCH: crc={crcname} range={rname} (0/{n} mismatches) ***")
                found_any = True
            elif mismatches < n * 0.05:
                print(f"  close: crc={crcname} range={rname} -- {mismatches}/{n} mismatches")

        # simple checksums
        for f in frames[:1]:
            pass
        simple_mismatch_counts = {}
        for f in frames:
            region = rfn(f)
            expected = f[-2:]
            expected1 = f[-1:]
            results = simple_checksums(region)
            for name, computed in results.items():
                key = f"{name}|{rname}"
                if len(computed) == 2:
                    ok = computed == expected
                else:
                    ok = computed == expected1
                simple_mismatch_counts.setdefault(key, 0)
                if not ok:
                    simple_mismatch_counts[key] += 1
        for key, mismatches in simple_mismatch_counts.items():
            if mismatches == 0:
                print(f"*** MATCH: {key} (0/{n} mismatches) ***")
                found_any = True
            elif mismatches < n * 0.05:
                print(f"  close: {key} -- {mismatches}/{n} mismatches")

    if not found_any:
        print("\nNo exact match found across any tested algorithm/range combination.")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "uart_monitor_poweron_sequence.log"
    frames = load_frames(path)
    test_frames(frames)
