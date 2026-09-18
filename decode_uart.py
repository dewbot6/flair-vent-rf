"""Decode a Saleae Logic 2 Async Serial CSV export (both UART directions) into
readable text lines and structured binary frames.

Frame format: `{` (0x7B), a 1-byte length N counting itself, N-1 more bytes
(the last two a CRC-16), then `}` (0x7D), often followed by \\r\\n. This looks
like a custom line-oriented binary protocol (brace-delimited so it's visible
in a plain terminal even though the payload itself is binary).

Run with: python3 decode_uart.py "<path to saleae export.csv>"
"""
import csv
import sys

import flair_frame


def load_channel_bytes(path):
    by_channel = {}
    with open(path) as f:
        reader = csv.DictReader(f)
        for r in reader:
            ch = r["name"]
            by_channel.setdefault(ch, []).append(
                (float(r["start_time"]), int(r["data"], 16), r["error"])
            )
    return by_channel


def find_frames(byte_stream):
    """byte_stream: list of (time, byte, error). Finds frames by their length
    byte and validates each with its CRC-16.

    Deliberately does NOT scan for the next 0x7D: the protocol doesn't escape
    its delimiters, so a body or CRC byte equal to 0x7D silently truncates the
    frame (this corrupted ~1% of real captures). The length byte plus a CRC
    check resolves it unambiguously. Returns (start_time, end_time,
    length_byte, content_bytes, crc_ok)."""
    frames = []
    i = 0
    n = len(byte_stream)
    while i < n:
        t, b, _ = byte_stream[i]
        if b == 0x7B and i + 1 < n:
            length_byte = byte_stream[i + 1][1]
            end = i + 1 + length_byte
            if length_byte >= 4 and end < n and byte_stream[end][1] == 0x7D:
                content = [byte_stream[k][1] for k in range(i + 1, end)]
                if flair_frame.check(bytes(content)):
                    frames.append((t, byte_stream[end][0], length_byte,
                                   content, True))
                    i = end + 1
                    continue
        i += 1
    return frames


def group_text_runs(byte_stream, min_len=4):
    """Find runs of printable-ish bytes (likely plain text log lines)."""
    runs = []
    cur = []
    cur_start = None
    for t, b, err in byte_stream:
        printable = (32 <= b <= 126) or b in (0x0D, 0x0A)
        if printable and not err:
            if not cur:
                cur_start = t
            cur.append(b)
        else:
            if len(cur) >= min_len:
                runs.append((cur_start, bytes(cur)))
            cur = []
    if len(cur) >= min_len:
        runs.append((cur_start, bytes(cur)))
    return runs


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "flair export.csv"
    by_channel = load_channel_bytes(path)

    for ch, stream in by_channel.items():
        print(f"\n{'='*60}\n{ch} ({len(stream)} bytes)\n{'='*60}")

        print("\n--- Text runs (printable-ish, likely log lines) ---")
        for t, text in group_text_runs(stream):
            decoded = text.decode("ascii", errors="replace").replace("\r\n", "\\r\\n ")
            print(f"  [{t:9.4f}s] {decoded!r}")

        print("\n--- Binary frames ({...}) ---")
        for t, t_end, length_byte, content, crc_ok in find_frames(stream):
            hexstr = " ".join(f"{b:02X}" for b in content)
            flag = "CRC ok" if crc_ok else f"CRC FAIL len-byte={length_byte} content={len(content)}"
            print(f"  [{t:9.4f}s -> {t_end:9.4f}s] [{flag}] {hexstr}")
