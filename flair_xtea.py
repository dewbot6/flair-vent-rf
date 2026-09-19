"""Flair RF application-layer crypto: XTEA-CTR.

Reverse-engineered from the CC430 firmware (see CLAUDE.md). The RF *frame* (PHY,
sync 0xD391, addresses, CRC) is handled by flair_frame.py / rf_receive.py; this
module is the *payload* cipher that sits on top.

Scheme (all confirmed in Ghidra; XTEA verified against the standard
key0/pt0 -> dee9d4d8f7131ed9 test vector):

  cipher     XTEA, 32 cycles (64 rounds), delta 0x9E3779B9
  mode       CTR. keystream block = XTEA_encrypt(v0=0x87654321, v1=counter)
             -> 8 keystream bytes (v0 little-endian then v1 little-endian)
             -> XOR into payload; counter += 1 per 8-byte block
  key        16 bytes = 4x little-endian uint32, key[0..3]
  counter    per-message; only its LOW BYTE is transmitted on air (the byte
             just before the ciphertext). The full value is >=16-bit session
             state, so decrypting a capture needs the high byte(s) too.

On-air packet layout (40-byte fixed capture from rf_receive.py):
  [0]      LEN
  [1:9]    addr1 (EUI-64)
  [9:17]   addr2 (EUI-64)
  [17]     type
  [18]     type/ctrl
  [19]     counter LOW byte          <-- v1 = (high<<8) | this
  [20:]    ciphertext (XTEA-CTR)

THE KEY IS NOT YET KNOWN. It could not be recovered from captures or the
main-flash image (see CLAUDE.md) -- most likely per-device in the CC430 info
flash. Get it with an SBW read of RAM 0x2830 on the running Puck:
    mspdebug ezfet "md 0x2830 0x10"
then paste the 16 bytes into KEY below (little-endian words are formed from the
raw byte order as read).
"""

DELTA = 0x9E3779B9
MASK = 0xFFFFFFFF
NONCE_V0 = 0x87654321

# 16 raw key bytes as read from RAM 0x2830 (fill in from the SBW read).
KEY_BYTES = None  # e.g. bytes.fromhex("0011223344556677889aabccddeeff00")


def _key_words(key_bytes):
    if key_bytes is None or len(key_bytes) != 16:
        raise ValueError("KEY_BYTES must be 16 bytes (read RAM 0x2830 via SBW)")
    return [int.from_bytes(key_bytes[i * 4:i * 4 + 4], "little") for i in range(4)]


def xtea_encrypt(v0, v1, k):
    s = 0
    for _ in range(32):
        v0 = (v0 + ((((v1 << 4) & MASK ^ (v1 >> 5)) + v1 & MASK) ^ (s + k[s & 3] & MASK))) & MASK
        s = (s + DELTA) & MASK
        v1 = (v1 + ((((v0 << 4) & MASK ^ (v0 >> 5)) + v0 & MASK) ^ (s + k[(s >> 11) & 3] & MASK))) & MASK
    return v0, v1


def keystream(counter, key_bytes, nbytes):
    """CTR keystream for a message starting at the given counter value."""
    k = _key_words(key_bytes)
    out = bytearray()
    ctr = counter & MASK
    while len(out) < nbytes:
        a, b = xtea_encrypt(NONCE_V0, ctr, k)
        out += a.to_bytes(4, "little") + b.to_bytes(4, "little")
        ctr = (ctr + 1) & MASK
    return bytes(out[:nbytes])


def crypt(payload, counter, key_bytes):
    """XOR payload with the keystream (symmetric: same call decrypts & encrypts)."""
    ks = keystream(counter, key_bytes, len(payload))
    return bytes(a ^ b for a, b in zip(payload, ks))


def decrypt_packet(pkt, key_bytes, counter_high=0):
    """Decrypt an on-air packet (bytes). counter = (counter_high<<8) | pkt[19].
    Returns the plaintext of the ciphertext region pkt[20:]."""
    counter = (counter_high << 8) | pkt[19]
    return crypt(pkt[20:], counter, key_bytes)


if __name__ == "__main__":
    # Self-test: XTEA against the published vector (no key needed).
    k0 = [0, 0, 0, 0]
    c = xtea_encrypt(0, 0, k0)
    ok = ("%08x%08x" % c) == "dee9d4d8f7131ed9"
    print("XTEA self-test:", "PASS" if ok else "FAIL", "%08x%08x" % c)
    if KEY_BYTES:
        print("KEY loaded; ready to decrypt captures.")
    else:
        print("KEY_BYTES not set -- read RAM 0x2830 via SBW and fill it in.")
