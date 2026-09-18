"""Build, parse and validate ESP8266<->CC430 UART frames for the Flair Puck.

Frame layout (content is what sits between the 0x7B / 0x7D delimiters):

    7B | LEN | ...body... | CRC_lo CRC_hi | 7D
         ^^^ LEN counts itself and both CRC bytes, i.e. LEN == len(content)

CRC parameters, recovered from 1120 captured frames (all verify):
    width=16  poly=0x1021  init=0x1021  xorout=0x0000
    reflect_in=False  reflect_out=False
    covers content[:-2] (the length byte through the last body byte,
    NOT the 0x7B/0x7D delimiters), stored little-endian

Note init == poly, which is why none of the crcmod predefined variants
(XMODEM/CCITT-FALSE/KERMIT, all init 0x0000 or 0xFFFF) ever matched.
"""

POLY = 0x1021
INIT = 0x1021


def crc16(data, init=INIT, poly=POLY):
    crc = init
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def check(content):
    """True if a frame's trailing CRC matches its body."""
    if len(content) < 4:
        return False
    return crc16(content[:-2]) == (content[-2] | (content[-1] << 8))


def build(body):
    """Wrap a body (everything after the length byte, excluding CRC) into a
    complete on-the-wire frame with correct length, CRC and delimiters."""
    length = len(body) + 3  # length byte itself + 2 CRC bytes
    if not 0 <= length <= 0xFF:
        raise ValueError(f"body too long for a 1-byte length field: {length}")
    content = bytes([length]) + bytes(body)
    c = crc16(content)
    return b"\x7b" + content + bytes([c & 0xFF, c >> 8]) + b"\x7d"


def reseal(content):
    """Recompute the CRC over a modified frame content, returning a full frame.

    Use this to edit a captured frame (e.g. change the position byte) and get
    a valid frame back without rebuilding it from scratch.
    """
    return build(bytes(content)[1:-2])


if __name__ == "__main__":
    import re
    import sys
    import glob

    FRAME_RE = re.compile(r"FRAME \(len=(\d+)\): ([0-9A-F ]+)")
    paths = sys.argv[1:] or glob.glob("uart_monitor_*.log")
    ok = bad = skipped = 0
    for path in paths:
        for line in open(path):
            m = FRAME_RE.search(line)
            if not m:
                continue
            length = int(m.group(1))
            d = bytes.fromhex(m.group(2).replace(" ", ""))
            if len(d) != length or length < 4 or d[0] != length:
                skipped += 1
                continue
            if check(d):
                ok += 1
            else:
                bad += 1
                print(f"CRC FAIL {path}: {d.hex(' ')}")
    total = ok + bad
    rate = (ok / total * 100) if total else 0.0
    print(f"\n{ok}/{total} frames pass CRC ({rate:.2f}%), {skipped} malformed skipped")
