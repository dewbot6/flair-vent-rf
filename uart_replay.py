"""Replay a captured ESP8266->CC430 UART command frame verbatim, to test
whether the CC430 will accept and act on it without the ESP8266 present.

Only safe to do with the ESP8266 board physically disconnected (otherwise
our TX would contend with the ESP8266 also driving the same URX line).

Run with: python3 uart_replay.py <serial_port> [baud]
"""
import sys
import time
import serial

# A real captured close-command frame (content between the { } delimiters,
# captured verbatim from a genuine ESP8266->CC430 transaction). Byte 28
# (0-indexed within this content) = 0x64, our confirmed "close" marker.
CLOSE_FRAME_CONTENT_HEX = (
    "38 00 11 00 01 00 00 00 01 00 00 12 4B 00 38 0D 16 FD 03 00 07 08 78 27 "
    "36 11 D3 FF 64 00 EE 02 00 00 00 00 00 00 0A 04 00 00 00 00 00 00 06 01 "
    "00 00 00 00 00 00 AB D4"
)

# A real captured open-command frame, for comparison/second test.
OPEN_FRAME_CONTENT_HEX = (
    "38 00 11 00 01 00 00 00 01 00 00 12 4B 00 38 0D 16 FD 03 00 25 08 78 27 "
    "36 61 D4 FF 00 00 EE 02 00 00 00 00 00 00 0A 04 00 00 00 00 00 00 06 01 "
    "00 00 00 00 00 00 0A A9"
)


def build_frame(content_hex):
    content = bytes.fromhex(content_hex.replace(" ", ""))
    return b"\x7b" + content + b"\x7d"


def listen(ser, seconds, label=""):
    print(f"--- listening {seconds}s {label} ---", flush=True)
    start = time.time()
    buf = b""
    while time.time() - start < seconds:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
    printable = bytes(b if 32 <= b <= 126 or b in (0x0D, 0x0A) else 0x2E for b in buf)
    print(printable.decode("ascii", errors="replace"), flush=True)
    print(f"RAW HEX: {buf.hex()}", flush=True)
    print(f"--- done listening, {len(buf)} bytes total ---", flush=True)
    return buf


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 uart_replay.py <serial_port> [baud] [open|close]")
        sys.exit(1)
    port = sys.argv[1]
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else 115200
    which = sys.argv[3] if len(sys.argv) > 3 else "close"

    ser = serial.Serial(port, baud, timeout=0.2)
    print(f"Opened {port} at {baud} baud.")

    # Listen briefly first to see baseline (idle) CC430 behavior, if any.
    listen(ser, 3, "(baseline, before sending anything)")

    frame = build_frame(CLOSE_FRAME_CONTENT_HEX if which == "close" else OPEN_FRAME_CONTENT_HEX)
    print(f"\nSending {which} frame ({len(frame)} bytes): {frame.hex()}", flush=True)
    ser.write(frame)
    ser.flush()

    listen(ser, 10, "(after sending command)")
    print("\nDone.")
