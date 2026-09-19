# flair-vent-rf

Reverse-engineering the Flair Smart Vent so it can be controlled directly from
Home Assistant, without Flair's cloud.

Two protocols are involved, and both have been worked out to differing depths:

1. The **UART link inside the Flair Puck**, between its ESP8266 (WiFi) and its
   CC430 (sub-GHz radio). Fully decoded — including proportional vent control.
2. The **915 MHz RF link** between Puck and vent. PHY and framing recovered;
   the application payload is encrypted.

Full engineering detail, including dead ends and corrections, lives in
[CLAUDE.md](CLAUDE.md). This file is the summary.

## What works today

**Direct proportional control of the vent over UART**, with the Puck's ESP8266
physically removed — no Flair firmware in the loop:

```bash
python3 cc430_drive.py /dev/cu.usbserial-XXXX --position 50
```

Verified at 0, 25, 50 and 100. The Flair app itself only exposes open/closed,
so intermediate positions are capability the stock product doesn't offer.

**Receiving the vent's RF traffic** with a YARD Stick One:

```bash
python3 rf_receive.py --seconds 60
```

## Protocol summary

### Puck-internal UART (ESP8266 ↔ CC430), 115200 8N1

```
7B | LEN | ...body... | CRC_lo CRC_hi | 7D      LEN counts itself and the CRC
```

| field | meaning |
|---|---|
| `byte[2]` | direction / flags |
| `byte[4:6]` | message type |
| `byte[6:8]` | source address (ESP8266 = `0x0001`, CC430 = `0x0000`) |
| `byte[8:10]` | destination address |
| `byte[10:18]` | device EUI-64 |
| `byte[22]` | **commanded position, 0–100** (`0x00` closed, `0x64` open) |

CRC-16: `poly 0x1021, init 0x1021, xorout 0x0000`, MSB-first, stored
little-endian, over `content[:-2]`. Verifies on 6109/6109 captured frames. The
non-standard init (equal to the polynomial) is why no stock CRC variant matched.

The position command is *desired state*, re-sent roughly every 6 seconds — not
a one-shot event.

### 915 MHz RF

```
Frequency    915.0 MHz          Preamble   0xAA
Modulation   2FSK               Sync word  0xD391, sent twice (32-bit)
Data rate    38400 bps          Packet     first byte = length
Deviation    ~20 kHz            Whitening  none
```

Frame layout is `LEN | addr1(8) | addr2(8) | body`, with IEEE EUI-64 addresses
(`00:12:4B` is the Texas Instruments OUI) and `FF×8` for broadcast.

The body opens with a type/control pair and a sequence counter, after which it
is **encrypted** — mean byte-difference of 0.997 between packets of the same
class and state, at ~7 bits/byte entropy.

## Status

| goal | state |
|---|---|
| UART protocol decoded | done |
| Proportional vent control via CC430 | done |
| RF PHY + framing | done |
| RF payload decode / forge | **blocked — encrypted** |
| Vent control with no Puck hardware | blocked on the above |

Controlling the vent over RF with no Puck requires key material from the
CC430, which makes a Spy-Bi-Wire firmware/register read the critical path. An
attempt with an eZ-FET LaunchPad reached the FET but not the target; lead
length and jumper wiring are the untested suspects, and the JTAG fuse has not
been ruled out.

## Tools

| script | purpose |
|---|---|
| `flair_frame.py` | UART frame CRC, build and reseal. Run directly to verify all capture logs. |
| `cc430_drive.py` | Drive the CC430 directly over USB-TTL, with pre-flight bus-contention checks. |
| `uart_monitor.py` | Live single-wire UART monitor. |
| `decode_uart.py` | Parse a Saleae Logic 2 Async Serial CSV export. |
| `saleae_capture.py` | Two-channel Logic 8 capture with timed toggle cues. |
| `analyze_capture.py` | Correlate captured frames against those cues to find command bytes. |
| `rf_receive.py` | Receive vent RF packets with a YARD Stick One. |
| `rf_sweep_constrained.py` | PHY sweep (superseded by the IQ measurement, kept for reference). |
| `dual_capture.py` | Simultaneous timestamped UART + RF capture. |
| `capture.py`, `burst_capture.py`, `freq_sweep.py` | Earlier RSSI-gated RF experiments. |

Raw captures (UART logs, RF packet dumps, Saleae exports) live in
[`captures/`](captures/). Scripts default to reading from and writing to that
folder.

## Hardware

- Flair Smart Vent 4x10 and a Flair Puck (the Puck is a capture aid, not part
  of the intended final system)
- YARD Stick One (CC1111) — RF receive and transmit
- RTL-SDR — raw IQ capture; this is what actually cracked the PHY
- 3.3V USB-TTL adapter (CH340) — the Puck's internal UART
- Saleae Logic 8 — two-channel UART capture
- MSP430 eZ-FET LaunchPad — Spy-Bi-Wire attempts

## A note on method

The RF PHY resisted months of parameter sweeping against the YARD Stick's
hardware demodulator, which makes an irreversible slicing decision from its
configured parameters — a wrong guess yields noise, and the signal is gone.

One RTL-SDR IQ capture settled it in a single session, because raw IQ can be
analysed offline indefinitely: an FFT gives the deviation directly from the
FSK lobe spacing, and packing demodulated bits at all eight bit offsets made
the preamble, sync word and payload visible in plain text.

The earlier sweeps had also produced a false negative on essentially the
correct parameters, because with sync-word detection disabled the receiver
never byte-aligns and a correctly demodulated packet still looks like noise.
Several later hypotheses were built on that mistake. Both errors are recorded
in CLAUDE.md rather than quietly fixed.
