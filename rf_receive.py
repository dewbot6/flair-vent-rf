"""Receive the Flair vent's 915 MHz packets with the YARD Stick One.

PHY recovered 2026-09-18 by capturing raw IQ with an RTL-SDR and measuring the
signal rather than sweeping a hardware demodulator against it. See CLAUDE.md.

The sync word is what previously made this look impossible: with sync detection
off the radio never byte-aligns, so even correct PHY parameters produce a
bit-shifted stream indistinguishable from noise -- which is exactly what an
earlier attempt at these very parameters concluded.

Run with:  python3 rf_receive.py [--port <uart>] [--seconds 60]
Passing --port also commands position changes, so transmissions are triggered
rather than waited for. Note the RF burst lags the UART command by 3-12s and
lasts only ~10ms.
"""
import argparse
import time

from rflib import (RfCat, MOD_2FSK, SYNCM_30_of_32, ChipconUsbTimeoutException)

FREQ = 915000000
DRATE = 38400
DEVIATION = 20000
CHAN_BW = 105000
SYNC_WORD = 0xD391      # transmitted twice -> 32-bit sync
PKT_LEN = 40

# Signatures shared with the UART protocol; their presence confirms a real packet.
CMD_SIG = bytes.fromhex("00124b00380d16fd")
HB_SIG = bytes.fromhex("46819ee3001d000f")


def configure(d):
    d.setModeIDLE()
    d.setFreq(FREQ)
    d.setMdmModulation(MOD_2FSK)
    d.setMdmDRate(DRATE)
    d.setMdmDeviatn(DEVIATION)
    d.setMdmChanBW(CHAN_BW)
    d.setMdmSyncWord(SYNC_WORD)
    d.setMdmSyncMode(SYNCM_30_of_32)
    d.setEnableMdmManchester(False)
    d.makePktFLEN(PKT_LEN)
    d.setModeRX()


def describe(pkt):
    bits = []
    if CMD_SIG in pkt:
        bits.append(f"cmd@{pkt.find(CMD_SIG)}")
    if HB_SIG in pkt:
        bits.append(f"heartbeat@{pkt.find(HB_SIG)}")
    return " ".join(bits) or "unrecognised payload"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", help="UART port; also command position changes to trigger TX")
    p.add_argument("--seconds", type=float, default=60.0)
    args = p.parse_args()

    ser = build = None
    if args.port:
        import serial
        import cc430_drive as cd
        ser = serial.Serial(args.port, 115200, timeout=0.2)
        build = cd.build_command

    d = RfCat()
    configure(d)
    print(f"RX: {FREQ/1e6:.1f}MHz 2FSK {DRATE}bps dev={DEVIATION} sync=0x{SYNC_WORD:04X}\n")

    n = 0
    pos = 100
    next_cmd = 0.0
    end = time.time() + args.seconds
    try:
        while time.time() < end:
            if ser and time.time() >= next_cmd:
                ser.write(build(pos))
                ser.flush()
                pos = 0 if pos == 100 else 100
                next_cmd = time.time() + 15.0
            try:
                pkt, _ = d.RFrecv(timeout=400)
            except ChipconUsbTimeoutException:
                continue
            if not pkt:
                continue
            n += 1
            print(f"[{n:3d}] len={pkt[0]:3d} {describe(pkt)}")
            print(f"      {pkt.hex(' ')}")
    finally:
        d.setModeIDLE()
        if ser:
            ser.close()
    print(f"\n{n} packets received.")


if __name__ == "__main__":
    main()
