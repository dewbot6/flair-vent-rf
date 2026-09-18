"""Drive the CC430 directly over UART, with the ESP8266 held in reset.

Sends the ESP8266's position command (byte[22] = 0..100) so the vent can be
commanded without the ESP, and watches the CC430's replies to see whether it
acted.

The command is desired STATE, not an event -- the real ESP re-sends it every
~6s until it changes -- so this repeats it on the same cadence rather than
firing once.

SAFETY, read before wiring:
  * The CC430F5136 is a 3.3V part and is NOT 5V tolerant. Use a 3.3V USB-TTL
    adapter. Listening is safe at any level; DRIVING a line is not.
  * Hold the ESP8266 in reset (its RST pin to GND) before connecting TX.
    Otherwise two drivers fight over the line the ESP normally drives.
  * Wire adapter TX -> the line the ESP transmits on (the CC430's receive),
    adapter RX -> the line the CC430 transmits on, GND -> Puck GND.

This script refuses to transmit until a listen-only phase confirms the CC430
is alive and the ESP has actually gone quiet.

Run with:  python3 cc430_drive.py <serial_port> --position 50
"""
import argparse
import sys
import time

import serial

import flair_frame as ff

CMD_SIG = bytes.fromhex("00124b00380d16fd")
POSITION_BYTE = 22        # commanded position, ESP -> CC430
REPORTED_BYTE = 28        # reported position, CC430 -> ESP. Same polarity as
                          # the command, and lags it by a few seconds.

# Frame CONTENT minus its CRC (54 bytes), taken verbatim from the real CLOSE
# command in saleae_out/uart_20260918_141537.csv. Verified by rebuilding both
# the CLOSE and OPEN frames from it and matching the captured CRCs exactly.
TEMPLATE = bytes.fromhex(
    "3800010005000100000000124b00380d16fd00000000000000003e00"
    "ff41040000009001140000000000000000000000000000000000"
)


def build_command(position):
    """Full on-the-wire frame commanding `position` (0..100).

    TEMPLATE is frame CONTENT minus its CRC, so the body handed to build() is
    TEMPLATE[1:] (everything after the length byte); build() re-adds the length
    byte, the CRC and the delimiters.
    """
    body = bytearray(TEMPLATE[1:])
    body[POSITION_BYTE - 1] = position   # content[22] == body[21]
    return ff.build(bytes(body))


def frames_from(buf):
    """Length-driven framing with CRC validation; returns (frames, leftover)."""
    out = []
    while True:
        i = buf.find(b"\x7b")
        if i < 0 or len(buf) < i + 2:
            break
        length = buf[i + 1]
        if length < 4:
            buf = buf[i + 1:]
            continue
        if len(buf) < i + length + 2:
            break
        content = bytes(buf[i + 1:i + 1 + length])
        if buf[i + 1 + length] == 0x7D and ff.check(content):
            out.append(content)
            buf = buf[i + length + 2:]
        else:
            buf = buf[i + 1:]
    return out, buf


def listen(ser, seconds, label):
    print(f"--- listening {seconds}s ({label}) ---", flush=True)
    buf = bytearray()
    frames = []
    end = time.time() + seconds
    while time.time() < end:
        chunk = ser.read(256)
        if chunk:
            buf.extend(chunk)
            got, buf = frames_from(buf)
            for f in got:
                src = f[6] | (f[7] << 8)
                who = {1: "ESP8266", 0: "CC430"}.get(src, f"0x{src:04X}")
                extra = ""
                if len(f) == 56 and f[10:18] == CMD_SIG:
                    idx = POSITION_BYTE if src == 1 else REPORTED_BYTE
                    extra = f"   <-- command frame, byte[{idx}]=0x{f[idx]:02X} ({f[idx]})"
                print(f"  {who:8s} len={len(f):3d} type=0x{f[4]:02X}{extra}", flush=True)
            frames.extend(got)
    return frames


def main():
    p = argparse.ArgumentParser()
    p.add_argument("port")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--position", type=int, required=True,
                   help="0..100. 0=closed, 100=open (ESP->CC430 polarity)")
    p.add_argument("--listen-first", type=float, default=15.0)
    p.add_argument("--repeat", type=float, default=6.0, help="resend interval")
    p.add_argument("--duration", type=float, default=60.0, help="how long to hold the state")
    p.add_argument("--force", action="store_true",
                   help="transmit even if the pre-flight checks fail")
    args = p.parse_args()

    if not 0 <= args.position <= 100:
        sys.exit("--position must be 0..100")

    ser = serial.Serial(args.port, args.baud, timeout=0.2)
    print(f"Opened {args.port} at {args.baud} baud.\n")

    pre = listen(ser, args.listen_first, "pre-flight, not transmitting yet")
    esp = [f for f in pre if (f[6] | (f[7] << 8)) == 1]
    cc = [f for f in pre if (f[6] | (f[7] << 8)) == 0]
    print(f"\npre-flight: {len(cc)} CC430 frames, {len(esp)} ESP8266 frames")

    problems = []
    if esp:
        problems.append(f"ESP8266 is still transmitting ({len(esp)} frames) -- "
                        "hold its RST to GND, or TX will contend with it")
    if not cc:
        problems.append("no CC430 frames heard -- check RX is on the CC430's "
                        "transmit line, GND is common, and the Puck is powered")
    if cc and not esp:
        # RX sits on the CC430's transmit line; the ESP drives the OTHER wire.
        # Seeing no ESP frames here is therefore expected whether or not the
        # ESP is actually in reset, and proves nothing about contention.
        print("  NOTE: no ESP frames seen, but RX is on the CC430's line -- the\n"
              "        ESP drives the other wire, so this does NOT prove it is\n"
              "        quiet. Verify separately (listen on the TX line, or watch\n"
              "        it with the Logic 8) before trusting this.")
    if problems:
        for m in problems:
            print(f"  FAIL: {m}")
        if not args.force:
            sys.exit("\nRefusing to transmit. Fix the above, or pass --force.")
        print("\n--force given; transmitting anyway.")

    frame = build_command(args.position)
    print(f"\nCommanding position {args.position} "
          f"(0x{args.position:02X}) every {args.repeat}s for {args.duration}s")
    print(f"  frame: {frame.hex(' ')}\n")

    end = time.time() + args.duration
    n = 0
    while time.time() < end:
        ser.write(frame)
        ser.flush()
        n += 1
        print(f"  [tx #{n}] sent", flush=True)
        listen(ser, min(args.repeat, max(0.0, end - time.time())), "reply window")
    print(f"\nDone. Sent {n} frames.")
    print(f"The CC430's reply byte[{REPORTED_BYTE}] should settle to {args.position}; "
          "it lags the command by a few seconds, so it may still show the "
          "previous value when this exits.")


if __name__ == "__main__":
    main()
