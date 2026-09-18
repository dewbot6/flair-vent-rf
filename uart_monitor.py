"""Live UART monitor for the Puck's ESP8266<->CC430 link, using a USB-TTL
adapter instead of the Saleae. Prints decoded text lines and brace-delimited
binary frames as they arrive, so you can watch traffic live while toggling
the vent from the app.

Frame format (confirmed from the Saleae capture): `{` (0x7B) + 1-byte length
N (counting itself) + (N-1) more bytes + `}` (0x7D). Last 2 bytes of content
are a CRC-16 (poly/init 0x1021, little-endian) -- see `flair_frame.py`.

Run with: python3 uart_monitor.py <serial_port> [baud]
e.g. python3 uart_monitor.py /dev/tty.usbserial-XXXX 115200

List available ports with: python3 -m serial.tools.list_ports
"""
import sys
import time
import serial

import flair_frame


def find_frames_live(buf):
    """Given a growing bytearray, extract complete {...} frames from the
    front and return (frames, remaining_buf). Also extracts any printable
    text found before the first '{' as a separate text chunk.

    Frames are delimited by length, NOT by scanning for the 0x7D terminator:
    the protocol does not escape its delimiters, so a body or CRC byte that
    happens to equal 0x7D would truncate the frame. (Measured: this silently
    corrupted ~1% of captured frames, since a CRC byte is 0x7D roughly 0.8%
    of the time.) The length byte plus a CRC check is unambiguous instead.
    """
    frames = []
    text_chunks = []
    while True:
        brace_idx = buf.find(b"\x7b")
        if brace_idx == -1:
            if len(buf) > 256:
                text_chunks.append(bytes(buf))
                buf = bytearray()
            break
        if brace_idx > 0:
            text_chunks.append(bytes(buf[:brace_idx]))
            buf = buf[brace_idx:]
        if len(buf) < 2:
            break
        length = buf[1]
        if length < 4:
            # can't be a real frame; this 0x7B was payload/text, resync past it
            text_chunks.append(bytes(buf[:1]))
            buf = buf[1:]
            continue
        if len(buf) < length + 2:
            break  # wait for the rest of the frame to arrive
        content = bytes(buf[1:1 + length])
        if buf[1 + length] == 0x7D and flair_frame.check(content):
            frames.append(content)
            buf = buf[length + 2:]
        else:
            # bad framing -- treat this 0x7B as data and resync on the next one
            text_chunks.append(bytes(buf[:1]))
            buf = buf[1:]
    return frames, text_chunks, buf


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 uart_monitor.py <serial_port> [baud]")
        sys.exit(1)
    port = sys.argv[1]
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else 115200

    ser = serial.Serial(port, baud, timeout=0.2)
    print(f"Listening on {port} at {baud} baud. Ctrl-C to stop.")

    buf = bytearray()
    start = time.time()
    try:
        while True:
            chunk = ser.read(256)
            if chunk:
                buf.extend(chunk)
                frames, text_chunks, buf = find_frames_live(buf)
                t = time.time() - start
                for tc in text_chunks:
                    printable = bytes(b if 32 <= b <= 126 or b in (0x0D, 0x0A) else 0x2E for b in tc)
                    decoded = printable.decode("ascii", errors="replace").replace("\r\n", "\\r\\n ")
                    if decoded.strip():
                        print(f"[{t:9.3f}s] TEXT: {decoded!r}")
                for f in frames:
                    hexstr = " ".join(f"{b:02X}" for b in f)
                    print(f"[{t:9.3f}s] FRAME (len={len(f)}): {hexstr}")
    except KeyboardInterrupt:
        print("\nStopped.")
