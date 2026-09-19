"""Forge and transmit a Flair vent RF command with the YARD Stick One.

The end goal: control the vent over RF with no Puck. This builds a command
frame, XTEA-CTR-encrypts the payload, wraps it in the PHY frame, and transmits.

BLOCKED ON THE KEY: set flair_xtea.KEY_BYTES first (SBW read of RAM 0x2830).
It is also NOT yet safe to fire blind -- before trusting this you must:
  1. Have the key (flair_xtea.KEY_BYTES).
  2. Confirm the plaintext payload layout by decrypting known-command captures
     with flair_rf_decrypt.py (which byte is the position, what the fixed fields
     are, and the exact counter the vent will accept next).
  3. Know the target vent's address (addr fields) and the current counter value
     the vent expects (replaying an old counter will likely be rejected).

Until (1)-(3) are pinned this module is a scaffold: it documents the frame it
will build and can print/though-not-yet-transmit a candidate packet so the
structure can be reviewed. Transmission is gated behind --send AND a set key.

PHY (from rf_receive.py): 915.0 MHz, 2FSK, 38400 bps, dev ~20 kHz, sync 0xD391
(sent twice), no whitening. Frame: LEN | addr1(8) | addr2(8) | type | ctrl |
counter_low | ciphertext(XTEA-CTR).
"""
import argparse
import sys

import flair_xtea

# Fill these once the corresponding fields are confirmed from decrypted captures.
POSITION_OFFSET = None   # byte offset of the position within the *plaintext* payload
TEMPLATE_PLAINTEXT = None  # a known-good plaintext payload to modify (from a decrypt)


def build_payload(position):
    if TEMPLATE_PLAINTEXT is None or POSITION_OFFSET is None:
        raise SystemExit(
            "Payload layout not yet known. Decrypt known-command captures with "
            "flair_rf_decrypt.py to find the position byte and a template payload,"
            " then set TEMPLATE_PLAINTEXT / POSITION_OFFSET here."
        )
    pt = bytearray(TEMPLATE_PLAINTEXT)
    pt[POSITION_OFFSET] = position & 0xFF
    return bytes(pt)


def build_frame(addr1, addr2, type_byte, ctrl_byte, counter, position):
    """Assemble the full on-air frame (without PHY preamble/sync/CRC, which the
    radio adds). Ciphertext = XTEA-CTR(plaintext, counter)."""
    pt = build_payload(position)
    ct = flair_xtea.crypt(pt, counter, flair_xtea.KEY_BYTES)
    body = bytes([type_byte, ctrl_byte, counter & 0xFF]) + ct
    frame = bytes([1 + 8 + 8 + len(body)]) + addr1 + addr2 + body
    return frame


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--position", type=int, required=True, help="0..100")
    p.add_argument("--counter", type=lambda x: int(x, 0), required=True,
                   help="next counter value the vent expects (full value)")
    p.add_argument("--addr1", required=True, help="16 hex chars (EUI-64)")
    p.add_argument("--addr2", required=True, help="16 hex chars (EUI-64)")
    p.add_argument("--type", type=lambda x: int(x, 0), default=0x60)
    p.add_argument("--ctrl", type=lambda x: int(x, 0), default=0x83)
    p.add_argument("--send", action="store_true", help="actually transmit (else print only)")
    args = p.parse_args()

    if flair_xtea.KEY_BYTES is None:
        sys.exit("KEY_BYTES not set in flair_xtea.py (read RAM 0x2830 via SBW first).")

    frame = build_frame(bytes.fromhex(args.addr1), bytes.fromhex(args.addr2),
                        args.type, args.ctrl, args.counter, args.position)
    print("frame:", frame.hex(" "))

    if not args.send:
        print("(dry run -- pass --send to transmit once the layout/counter are confirmed)")
        return
    from rflib import RfCat
    import rf_receive
    d = RfCat()
    rf_receive.configure(d)     # same PHY
    d.setModeTX()
    d.RFxmit(frame)
    d.setModeIDLE()
    print("transmitted.")


if __name__ == "__main__":
    main()
