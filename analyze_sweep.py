"""Analyze a sweep_configs.py log: for each PHY config, check whether captured
payloads show repeated prefixes (real preamble/sync structure) or look like
random noise (flat histogram, no repeated prefixes).

Run with: python3 analyze_sweep.py <logfile>
"""
import re
import sys
import statistics
from collections import Counter, defaultdict


def analyze(logfile):
    payloads_by_config = defaultdict(list)
    current_config = None

    with open(logfile) as f:
        for line in f:
            m = re.match(r".*sweep:(.+?) RSSI spike", line)
            if m:
                current_config = m.group(1)
                continue
            m = re.match(r"\s*->\s*(\d+)\s+([0-9a-f]+)", line)
            if m and current_config:
                payloads_by_config[current_config].append(bytes.fromhex(m.group(2)))

    for config, payloads in payloads_by_config.items():
        print(f"\n=== {config} ===")
        print(f"  payloads: {len(payloads)}")
        if not payloads:
            continue
        prefix4 = Counter(p[:4].hex() for p in payloads)
        top_prefix, top_count = prefix4.most_common(1)[0]
        print(f"  most common 4-byte prefix: {top_prefix} x{top_count} (out of {len(payloads)})")
        overlaps = 0
        for i in range(len(payloads) - 1):
            a, b = payloads[i], payloads[i + 1]
            for shift in (1, 2, 4):
                if a[-shift:] == b[:shift]:
                    overlaps += 1
                    break
        print(f"  adjacent tail==head overlaps: {overlaps} / {max(len(payloads)-1,1)}")
        allbytes = b"".join(payloads)
        hist = Counter(allbytes)
        counts = list(hist.values())
        print(f"  byte histogram: {len(hist)} distinct values, mean={statistics.mean(counts):.1f}, "
              f"stdev={statistics.pstdev(counts):.1f}")
        verdict = "LIKELY REAL STRUCTURE" if top_count > max(3, len(payloads) * 0.05) else "looks like noise"
        print(f"  verdict: {verdict}")


if __name__ == "__main__":
    analyze(sys.argv[1] if len(sys.argv) > 1 else "sweep_output.log")
