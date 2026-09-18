# Flair Smart Vent RF Reverse Engineering

## Goal
Reverse-engineer the proprietary 915 MHz RF protocol between a Flair Smart Vent
and its Puck hub, so the vent can eventually be controlled directly from Home
Assistant via a USB dongle -- no Puck required in the final deployment.

End state: a USB-connected sub-GHz radio (currently a YARD Stick One) plugged
into the Home Assistant host, running a script/service that can open, close,
and set position on the vent by replaying/forging its native RF packets.

## Hardware in hand
- **Flair Smart Vent, 4x10** (duct opening size -- confirmed by removing the
  existing register and measuring the boot directly; the outer faceplate
  measured ~6x11" but that's the flange, not the duct size).
- **Flair Puck** -- bought *temporarily*, purely to generate real pairing/
  command RF traffic to capture. Not intended to be part of the final
  deployed system.
- **YARD Stick One** (Great Scott Gadgets) -- CC1111-based sub-GHz USB
  transceiver, running stock rfcat firmware. This is the capture/replay tool.
- Development happening on a **MacBook Pro (macOS)** first; working code will
  later move to whatever host runs Home Assistant.

## Software environment (macOS, already set up)
```bash
brew install libusb
pip3 install rfcat --break-system-packages
```
Confirmed working:
```bash
python3 -c "import rflib; print('rflib OK')"   # -> rflib OK
rfcat -r                                        # drops into IPython w/ `d` = dongle
```
Python 3.14.7, IPython 9.16.1. rfcat firmware rev 0606, rflib rev 631.

`d.ping()` confirms the dongle is alive and responding.

## What we know about the target protocol
- FCC filing (ID **2AK78VENTO**, grantee Standard Euler Inc. = Flair's legal
  name) confirms operation at **905.0-925.0 MHz**.
- Flair's own spec sheets say "915MHz Radio for device to device
  communication." Wi-Fi lives only at the Puck/Bridge level; the vent itself
  only speaks this proprietary sub-GHz link (plus IR, unrelated, for AC units).
- Community speculation (unconfirmed) from Flair's Home Assistant integration
  discussions suggests the protocol may be **DASH7** (ISO/IEC 18000-7,
  D7A/D7AP), which is typically built on CC1101/CC430-class radios with GFSK
  modulation at defined channel classes:
  - Lo Rate: ~9.6 kbps GFSK
  - Normal Rate: ~55.555 kbps GFSK
  - Hi Rate: ~166.666 kbps GFSK
  Treat this as a hypothesis to test, not a confirmed fact. No teardown has
  confirmed the exact radio chip on Flair's PCB, and no public rtl_433 or URH
  decoder profile exists for Flair specifically (checked, none found).
- No Puck teardown/protocol documentation exists publicly. This is genuinely
  unclaimed reverse-engineering territory.

## FCC filing data (authoritative -- pulled 2026-08-22)
FCC ID **2AK78VENTO** (RF Exposure Info doc 6836699, Test Report doc 6836698,
report SZCR230700230901, both saved locally: see `fcc_test_report.pdf` in this
folder). Straight from the filing, not speculation:
- **Modulation Type: GFSK**
- **Operation Frequency: 915MHz**
- **Number of Channels: 1** (single fixed channel, NOT frequency hopping)
- Power supply: 3.0V DC (2x alkaline C cell batteries)
- Antenna: Planar Inverted-F, 4.1dBi gain, integrated on main PCB

Section 7.1 (20dB/Occupied Bandwidth test) spectrum analyzer scans across all
4 tested models (VENT-6x14, VENT-8x8, VENT-10x10, VENT-12x12) give the real,
measured signal:

| Model | Center Freq | Occupied BW | -20dB BW |
|---|---|---|---|
| VENT-6x14 | 914.97 MHz | 94.267 kHz | 83.53 kHz |
| VENT-8x8 | 914.97 MHz | 93.603 kHz | 91.91 kHz |
| VENT-10x10 | 915.0 MHz | 93.483 kHz | 94.07 kHz |
| VENT-12x12 | 915.0 MHz | 96.180 kHz | 88.68 kHz |

**This confirms the real carrier is at 915.0 MHz** (914.97-915.0 MHz across
units, normal crystal error) -- the 906-908MHz cluster found by
`freq_sweep.py` was very likely unrelated ambient interference, not the vent.
Trust the FCC filing's spectrum analyzer data over our own coarse RSSI sweep.

Occupied bandwidth is consistently ~93-96 kHz. This closely matches a
well-known TI CC1101/CC1111 SmartRF Studio reference preset that shows up
literally in the rflib source comments: **38.4 kbps datarate, 20kHz
deviation, 94kHz RX filter bandwidth**. Cheap IoT devices very often ship
with a stock preset rather than a custom-tuned config, so this is a strong,
specific hypothesis to test next -- not yet confirmed.

## rflib API confirmed available on this install
Full list confirmed via `dir(d)` on the actual installed rflib (matches what
was assumed when scripts were first drafted -- no surprises):

Key methods in use: `setModeIDLE`, `setModeRX`, `setModeTX`, `setMdmModulation`,
`setMdmDRate`, `setMdmDeviatn`, `setMdmChanBW`, `setFreq`, `setMdmSyncMode`,
`setEnableMdmManchester`, `RFrecv(timeout=1000, blocksize=None)`, `RFxmit`,
`getRSSI`, `ping`, `reprRadioConfig`, `getMdmSyncWord`, `setPktPQT`.

`RFrecv` timeout is in **milliseconds**, raises `ChipconUsbTimeoutException` on
timeout (must import/catch this from `rflib`).

## RSSI conversion (CC1111)
`d.getRSSI()` returns a raw signed byte, NOT dBm directly. Convert with:
```python
def rssi_dbm(raw_byte):
    val = raw_byte[0]
    if val >= 128:
        val -= 256
    return (val / 2) - 74
```
Measured ambient noise floor at 915 MHz / 55.555k GFSK config: **roughly -92
to -100 dBm, centered around -96 dBm**. Use `floor + 15` (~-81 dBm) as a
starting squelch threshold to distinguish real signal from noise -- adjust
once real Puck/vent traffic is observed.

## IMPORTANT lesson learned: don't blind-loop RFrecv without a squelch
Early attempts ran `RFrecv` in a tight `while True` loop with
`setMdmSyncMode(0)` (no sync-word qualification) and fixed 255-byte packet
mode. This returns a continuous firehose of noise-floor garbage (255-byte
chunks nonstop), not real packets, and can be bad enough that Ctrl-C doesn't
land in time to stop it (terminal gets flooded). Lessons:
- **Never loop with `while True`** -- always cap iterations
  (e.g. `while count < 2000`) so it self-terminates even if interrupt fails.
- **Gate captures on RSSI threshold**, not raw continuous `RFrecv` polling,
  until sync word / packet framing is actually known.
- If a session hangs unrecoverably: try Ctrl-C repeatedly, then Ctrl-\
  (SIGQUIT), then in a new terminal `ps aux | grep rfcat` -> `kill -9 <PID>`,
  then physically unplug/replug the YARD Stick One before restarting.

## Candidate PHY configs to sweep (none confirmed yet)
```python
CANDIDATE_CONFIGS = [
    # (label, modulation, datarate_bps, deviation_hz, channel_bw_hz, center_mhz)
    ("D7A-like Normal Rate (55.555k GFSK)", MOD_2FSK,    55555,  50000, 100000, 915.0),
    ("D7A-like Lo Rate (9.6k GFSK)",        MOD_2FSK,     9600,  25000,  62500, 915.0),
    ("D7A-like Hi Rate (166.6k GFSK)",      MOD_2FSK,   166666,  75000, 200000, 915.0),
    ("Generic OOK 4.8k",                    MOD_ASK_OOK,  4800,      0, 100000, 915.0),
]
```
**Note on ChanBW:** the CC1111 only supports 16 discrete channel bandwidths
(`24e6 / (8*(4+m)*2^e)` for m,e in 0..3 -- floor ~53.6kHz at the 24MHz
crystal). `setMdmChanBW` silently rounds an achievable request to the
nearest discrete value, but raises `Exception("ChanBW does not translate
into acceptable parameters...")` if the request is below the floor (e.g. the
original 50000Hz guess for Lo Rate). `capture.configure()` now bumps the
requested bw up in 5% steps until it finds one the radio accepts, so a bad
guess degrades gracefully instead of crashing a whole sweep.

## Current status / next steps
1. [DONE] Dongle verified working on macOS.
2. [DONE] rflib API confirmed to match assumptions -- no script changes needed
   for method names.
3. [DONE] Noise floor baseline measured (~-96 dBm) on the 55.555k GFSK config.
4. [DONE] RSSI-gated capture loop built (`capture.py`) -- confirmed silent
   with vent unpowered/Puck off before bringing traffic sources into the
   picture (0 hits baseline).
5. [DONE] Captured Puck<->vent pairing (vent powered, `vent_pairing_capture_powered.log`,
   1400 hits, strong -40 to -52 dBm signal -- unambiguously real RF, not noise).
6. [DONE] Coarse 905-925MHz RSSI sweep (`freq_sweep.py`) found real carrier
   energy centered ~906-908 MHz, not 915.0 MHz as hypothesized. But a
   longer, weaker (-68 to -80dBm) capture there during relaxed toggling
   looked more like ambient interference than the vent itself -- inconclusive,
   don't over-trust this as the confirmed vent frequency yet.
7. [KEY FINDING] Swept all 4 CANDIDATE_CONFIGS (Normal/Lo/Hi Rate GFSK, OOK)
   at both 915.0 and 906.5 MHz. Every config produces zero repeated-prefix
   structure across captures. More importantly, ran a byte-level
   autocorrelation check (see `analyze` commands in project history) on the
   strongest, most-real dataset (the 1400-hit pairing capture) -- **no lag
   from 1-32 bytes shows more than 1.07x the random baseline match rate**.
   This isn't "wrong FSK parameters" noise -- it's a total absence of
   periodic structure even in a capture we're highly confident is real
   signal. Combined with strong correlated RSSI + zero decodable structure
   across every reasonable FSK/GFSK/OOK config, **the leading hypothesis is
   now that the vent uses chirp spread spectrum (CSS / LoRa-style)
   modulation**, not simple FSK/OOK. A CC1111 (YARD Stick One) cannot
   demodulate CSS at all regardless of tuning -- it would output exactly
   this kind of structureless noise from a real chirp signal, since chirp
   demod requires dechirping/correlation, not a frequency discriminator.
8. [NEXT] Validate the CSS/LoRa hypothesis. Options, roughly in order of
   effort: (a) get physical access to the Puck or vent PCB and read chip
   markings directly (fastest, most certain way to settle this); (b) borrow
   or buy LoRa-capable hardware (SX1276/RFM95 module, or a HackRF/RTL-SDR +
   `gr-lora`/similar) and repeat the 906-908MHz capture with a proper CSS
   demodulator; (c) capture raw IQ (not demodulated bytes) with a wideband
   SDR and visually inspect the spectrogram for chirp sweeps (LoRa chirps
   have a very distinctive rising/falling sawtooth signature in a waterfall
   plot -- this would confirm/deny the hypothesis without needing to fully
   decode anything).
9. [SUPERSEDED] The CSS/LoRa hypothesis above is likely wrong -- the FCC
   filing (see new section below) directly confirms GFSK modulation, not
   chirp spread spectrum. Kept for the record but don't pursue it.
10. [DONE] Pulled the FCC filing (2AK78VENTO) directly: confirmed GFSK,
    915.0MHz, single channel, and real spectrum-analyzer occupied bandwidth
    (~93-96kHz across 4 tested models). See "FCC filing data" section above.
11. [DONE] Tested a config derived from that occupied bandwidth (guessed
    38.4kbps/20kHz deviation matching a common TI reference preset) at
    915.0MHz. Strong real signal (-40 to -80dBm) but still zero
    autocorrelation structure (max 1.06x random baseline) -- consistent with
    the payload being data-whitened (very common, XORs payload with a PRBS
    so even correctly-demodulated real data looks statistically random;
    only the preamble/sync word stay unwhitened).
12. [DONE] Tried reconstructing full continuous bursts (concatenating
    consecutive RFrecv reads) to search for the preamble/sync transition
    directly rather than relying on disjoint 255-byte snapshots. Found a
    real statistical excess of alternating 0xAA/0x55 preamble-like bytes
    (~8x more than random chance predicts across ~39KB), suggesting we are
    picking up genuine (whitened) protocol data -- but the pattern
    locations were scattered, not clustered at a consistent offset, so this
    didn't give a usable packet boundary.
13. [INCONCLUSIVE -- retest needed] Attempted to test the DASH7 hypothesis
    directly: DASH7 spec defines standardized, UNWHITENED 16-bit PHY sync
    words (Class 0/background = 0xE6D0, Class 1/foreground = 0x0B67).
    Configured the CC1111's hardware sync-word correlator (`setMdmSyncWord`
    + `setMdmSyncMode(SYNCM_15_of_16)`) to search for 0x0B67 directly --
    this would bypass the whitening problem entirely if the hypothesis is
    right. Got only 7 hits in 180s with zero byte-position agreement across
    any of them -- BUT the vent was never actually toggled during that run
    (user confirmed after the fact), so this test never fed it a real
    signal and is NOT a valid test of the hypothesis either way. The 7 hits
    just confirm ambient noise can occasionally trip the 15-of-16 tolerant
    correlator; says nothing about whether it'd catch a real transmission.
    RETESTED with actual toggles (same config, same 180s window, real
    open/close actions this time): got 8 hits vs. 7 in the no-toggle
    control run -- statistically the same rate, meaning toggling produced
    no measurable increase in sync-qualified hits. Still zero byte-position
    agreement across the 8 hits (max 2/8, matching pure chance). This is
    now a clean, valid, properly-controlled negative result: DASH7 sync
    word 0x0B67 (foreground/Class 1, 15-of-16 tolerance) does not correlate
    with real vent activity. DASH7 hypothesis considered closed for now
    (not proven impossible, but not worth further pursuit without new
    information).
14. [NEXT] Blind RF parameter-guessing has now genuinely run its course --
    frequency and modulation type are FCC-confirmed, but framing/sync
    remains unknown after a clean, controlled negative test of the DASH7
    hypothesis. Physical teardown (read the actual radio IC part
    number/markings off the Puck or vent PCB) is the highest-leverage next
    step -- exact datasheet register settings or a known chip with existing
    tools, instead of continuing to infer blind.
15. [LATER] Once framing is understood, write TX replay/forge routines with
    `RFxmit`.
16. [LATER] Port working capture+control logic to whatever host runs Home
    Assistant; decide then whether the dongle physically moves there or stays
    on the Mac and bridges over MQTT/network.

## Current gated capture script (paste into `rfcat -r` shell, or run as script)
```python
import time
from rflib import *

def rssi_dbm(raw_byte):
    val = raw_byte[0]
    if val >= 128:
        val -= 256
    return (val / 2) - 74

def configure(label, mod, datarate, deviation, bw, freq_mhz):
    d.setModeIDLE()
    d.setMdmModulation(mod)
    d.setMdmDRate(datarate)
    if mod == MOD_2FSK:
        d.setMdmDeviatn(deviation)
    d.setMdmChanBW(bw)
    d.setFreq(int(freq_mhz * 1_000_000))
    d.setMdmSyncMode(0)
    d.setEnableMdmManchester(False)
    d.setModeRX()
    print(f"Configured: {label}")

floor = -96
threshold = floor + 15  # adjust once real traffic observed

def gated_capture(max_iters=2000, note=""):
    count = 0
    hits = 0
    while count < max_iters:
        rssi = rssi_dbm(d.getRSSI())
        if rssi > threshold:
            ts = time.time()
            print(ts, note, "RSSI spike:", rssi)
            try:
                data, _ = d.RFrecv(timeout=200)
                print("  ->", len(data), data.hex())
                hits += 1
            except ChipconUsbTimeoutException:
                pass
        count += 1
    print(f"Done. {hits} captures out of {count} iterations.")

# Example usage:
# configure("D7A-like Normal Rate", MOD_2FSK, 55555, 50000, 100000, 915.0)
# gated_capture(2000, note="baseline silence check, puck off")
```

## MAJOR BREAKTHROUGH: Puck teardown + UART sniffing (2026-09)

Physically opened the Puck. Two PCBs inside:
- One board: ESP-12F (ESP8266) + display -- handles WiFi/cloud connectivity.
- Other board: **TI CC430F5136** (confirmed against the real CC430F513x
  datasheet, 48-pin RGZ package) -- this is the actual sub-1GHz radio, an
  MSP430 MCU with a CC1101-core radio integrated on-die. Test points found:
  GND, VCC, Button, I2C_CL, I2C_DA, URX, MCLK, TEST, ENC_B, ENC_A, 5V, UTX,
  MDIO. `TEST` = SBWTCK and (almost certainly) `MDIO` = RST/NMI/SBWTDIO --
  together these are a full Spy-Bi-Wire debug interface (pins 39/40 on the
  datasheet), a possible future path to a full firmware dump if ever needed.

**URX/UTX are a plain UART link between the ESP8266 and the CC430**,
115200 baud, 8N1, no parity, LSB-first, non-inverted -- confirmed by
decoding real ASCII boot log text at that exact config. Sniffed first with
a Saleae Logic 8 (2-channel simultaneous capture, safe since it's pure
listen-only), later with a single USB-TTL adapter for continued monitoring
(RX-only, GND, **never connect TX** -- this is a live 2-device bus, adding
a driving TX would cause bus contention with whichever chip already drives
that line).

Boot log confirms the ESP8266 board (test point group "UTX"/Channel 0 in
the Saleae capture) prints: custom app boot messages ("System init...",
"Global constructors invoked", "EZFLAG:5dc6", "RELEASE: Device type
detected:2"), then standard ESP8266 NONOS SDK WiFi driver text ("mode :
sta(mac)", "add if0", "scandone", "no Gipsy Danger IOT found, reconnect
after 1s"). "Gipsy Danger IOT" is presumably an internal/joke SSID or
service name, not something to read into further.

### Confirmed binary frame protocol (ESP8266 <-> CC430 internal link)

Frame format: `{` (0x7B) + 1-byte length N (**counts itself**, i.e. total
frame content including this byte) + (N-1) more bytes + `}` (0x7D). Last 2
content bytes are a **CRC-16, now fully cracked -- see below**.
Byte 1 of content (right after the length byte) is `00`, byte 2
is a **direction/source marker**: `01` on ESP8266-originated frames
(labelled "Async Serial"/Channel 0 in Saleae, no text ever seen on CC430's
side), `11` on CC430-originated frames (Channel 1, carries the actual
command/status traffic and "TX succeeded" text).

Two frame types seen so far on the CC430->ESP8266 side (Channel 1 / URX):
1. **Steady-state status frame** (56 bytes), repeats every few seconds
   basically unchanged, e.g.:
   `38 00 11 00 01 00 00 00 01 00 46 81 9E E3 00 1D 00 0F 20 00 <2-byte
   counter> 75 27 <2 bytes> 00 00 00 00 00 00 61 07 73 62 <varies> 00 <1-2
   varies> 00 00 88 00 00 98 00 9F 00 09 00 00 00 <2 bytes> <2-byte
   checksum>`. Looks like general telemetry/heartbeat, not itself the
   command.
2. **Command/transaction frame** (56 bytes), appears only right when a
   real open/close command is issued, always with this distinctive header:
   `38 00 11 00 01 00 00 00 01 00 00 12 4B 00 38 0D 16 FD 03 00 ...`.
   Appears ~2-5 seconds before a `TX succeeded` text line -- this is almost
   certainly the ESP8266 (or CC430 relaying back) confirming the actual
   over-the-air RF transmission to the vent.

### [2026-09-18] Header structure: it's a src/dst addressed bus

Byte 2 isn't just a direction flag -- the header carries real 16-bit
source/destination addresses, and they swap cleanly with direction. Verified
100% consistent across all 1278 CRC-valid frames:

```
byte0    LEN (counts itself)        byte4-5  message type (16-bit LE)
byte1    00                         byte6-7  SOURCE addr
byte2    dir/flags                  byte8-9  DEST addr
byte3    00
```

| direction | byte2 | src | dst | frames |
|---|---|---|---|---|
| ESP8266 -> CC430 | `0x01` | `0x0001` | `0x0000` | 168 |
| CC430 -> ESP8266 | `0x11` | `0x0000` | `0x0001` | 1110 |

So **ESP8266 = node 0x0001, CC430 = node 0x0000**, and `0x11` looks like
`0x01` plus a `0x10` "from node 0" / response flag.

Direction confirmed the unambiguous way: `uart_monitor_live.log` is the only
capture containing ESP8266 NONOS SDK text (`scandone`, `mode : sta`,
`add if0`, `Gipsy Danger`), and every frame in it is `0x01`/src=`0x0001`.
All other logs are `0x11` and carry the `TX succeeded` text (the CC430
reporting its own RF transmit). Each capture only ever sniffed ONE wire.

**Consequence: `uart_replay.py` replays the wrong direction.** Its captured
open/close frames are `0x11`, src=`0x0000`, dst=`0x0001` -- i.e. addressed
*to* the ESP8266, from the CC430. Sending those at the CC430 is telling it
"here is a message from yourself, addressed to someone else", which it should
ignore. Any direct-drive attempt needs `0x01`/src=`0x0001`/dst=`0x0000`
frames instead.

### [SOLVED 2026-09-18] The ESP->CC430 position command, captured at last

Two-channel Saleae capture (`saleae_out/uart_20260918_141537.*`, 420s, 8 cued
toggles) finally caught the ESP8266 -> CC430 command. The two frames differ in
**exactly one byte** (plus the CRC):

```
CLOSE  38 00 01 00 05 00 01 00 00 00 00 12 4b 00 38 0d 16 fd 00 00 00 00 00 00 ...  ba 74
OPEN   38 00 01 00 05 00 01 00 00 00 00 12 4b 00 38 0d 16 fd 00 00 00 00 64 00 ...  5f bb
                                                                         ^^ byte[22]
```

**byte[22] of the ESP->CC430 command frame is the target position:
`0x00` = CLOSED, `0x64` (100) = OPEN.** Scored 6/6 across every window where
a command was actually present (the first two cues produced no command frames
at all -- the Puck was still connecting).

Cross-check: forging `byte[22]=0` and `byte[22]=100` with `flair_frame.reseal()`
reproduces the captured CRCs `ba 74` and `5f bb` byte-for-byte.

**The command is desired STATE, not an event.** The ESP re-sends this frame
every ~6 seconds with the current target until it changes -- it does not fire
once per button press. That matters twice over: it's why a "look for a rare
frame" heuristic finds nothing, and it means driving the CC430 directly is a
matter of repeating a state frame rather than timing a one-shot.

Cloud round-trip latency from app tap to changed byte measured at **~9s**.

#### `byte[28]` of the CC430 reply = REPORTED position, same polarity

Settled by direct experiment: when commanded to 100 the reply's `byte[28]`
became `0x64`; when commanded to 50 it became `0x32`. It echoes the commanded
position on the **same** scale as `byte[22]`, and **lags by a few seconds**.

Two earlier claims in this file about this byte were wrong and are withdrawn:
1. That it had *inverted* polarity (`0x00` = open). It does not.
2. That it was *not a position field at all*. It is.

Both errors came from reading tiny, lagging samples: ~9 CC430-side frames
split 5x`0x64`/2x`0x00`, and a later live window where it appeared pinned at
`0x00` because the sampled frames fell either side of the transition rather
than during it. The `0x00`-while-closed observation that triggered the second
retraction is in fact *consistent* with same-polarity (0 = closed) -- it was
misread as contradictory.

Lesson worth keeping: this byte lags the command, and the frames carrying it
are sparse (one every ~25s). Sparse + lagging is exactly the combination that
manufactures spurious correlations in short windows. Prefer a commanded
experiment over passive observation when a field is this slow.

#### [CONFIRMED] `byte[22]` = commanded position, verified physically

`byte[22]` of the **ESP8266 -> CC430 command frame** is the commanded
position. It is the *only* byte differing between the captured OPEN and CLOSE
command frames, and scored 6/6 across every window containing a command.

Absolute mapping confirmed 2026-09-18 by listening on the ESP8266's transmit
line while toggling from the app and watching the vent itself:

| `byte[22]` | vent, physically observed |
|---|---|
| `0x64` (100) | **OPEN** |
| `0x00` (0) | **CLOSED** |

Both held steady across 16 and 11 consecutive frames respectively, with the
transition landing within seconds of the app command. This also confirms
directly that the command is **desired state re-sent every ~6s**, not a
one-shot event -- the same value repeats indefinitely until it changes.

So the earlier worry about inverted open/close was unfounded for this byte:
the Saleae cue labels were correct, and `0x64 = open` reads naturally as
"% open". (The retraction above concerns `byte[28]` of the CC430's *reply*,
which is a different field and is not a position at all.)

### [MILESTONE 2026-09-18] Direct control achieved, and the scale is PROPORTIONAL

The ESP8266 board was removed entirely and the CC430 board powered from the
USB-TTL adapter. `cc430_drive.py` then commanded the vent directly with forged
frames -- no Puck firmware in the loop at all.

**Confirmed working at four points: 0, 25, 50 and 100**, each producing the
corresponding physical vent position (closed, quarter, half, open).

`byte[22]` is therefore a genuine **0-100 percentage, not a binary flag** --
a capability the Flair app itself does not expose, since it only offers
open/closed. This answers the question left open since the first UART
captures.

Working setup for reference:
- ESP8266 board removed (its RST is sandwiched between the PCBs and
  unreachable, so holding it in reset was never an option -- removal was)
- CC430 board powered from the adapter
- Adapter RX on the CC430's transmit test point, TX on its receive test point.
  If nothing arrives at all, the wires are the likely cause: RX must be on the
  line the CC430 *drives*. Silence looks identical to a dead chip.
- `.venv/bin/python cc430_drive.py /dev/cu.usbserial-XXXX --position 50`

Note the CC430 emits a `len=16 type=0x0F` frame repeatedly with no ESP8266
present; not investigated, plausibly a host-absent or retry notice.

Still unconfirmed: whether intermediate values actually work. Only 0 and 100
have ever been observed, because the app exposes no percentage control. That's
now directly testable by forging `byte[22]=50` -- see next steps.

### [HISTORICAL -- now solved, see above] The ESP->CC430 command gap

The `0x64`/`0x00` position byte at offset 28 lives in **CC430 -> ESP8266**
frames -- the CC430 *reporting*, not the ESP *commanding*. The presumed flow:

1. ESP8266 gets a command from the cloud
2. **ESP8266 -> CC430: "move to position X"  <-- NEVER CAPTURED**
3. CC430 transmits over RF to the vent
4. CC430 -> ESP8266: status incl. position, then `TX succeeded` text

We have 4751 frames of step 4 and only 168 frames of the step-2 direction --
and those 168 contain just **4 unique frames**, none with a varying position
byte (24 of them are byte-identical repeats of a command-header frame that
looks like a periodic poll, not a state change). So the vent was almost
certainly never toggled during that one 358s capture.

**This is the single blocking unknown.** Everything downstream -- forging a
command, driving the CC430 directly, and getting a repeatable on-demand RF
trigger to aim the YARD Stick One at -- depends on capturing step 2.

### [SOLVED 2026-09-18] Frame CRC-16 cracked -- we can now forge frames

The trailing 2 bytes are a CRC-16 with these parameters:

| Parameter | Value |
|---|---|
| width | 16 |
| polynomial | `0x1021` |
| **init** | **`0x1021`** |
| xorout | `0x0000` |
| reflect in / out | false / false (MSB-first) |
| covered range | `content[:-2]` -- the length byte through the last body byte, **excluding** the `0x7B`/`0x7D` delimiters |
| stored as | **little-endian** (low byte first) |

**Verified on 6109/6109 well-formed frames (100%)** across every
`uart_monitor_*.log` capture, plus the two hand-captured open/close command
frames in `uart_replay.py` that were never part of the log corpus.

Why the earlier brute-force attempts (`crack_checksum.py`,
`crack_checksum2.py`) missed it: **`init` == the polynomial, `0x1021`.** Every
`crcmod` predefined CRC-16 variant uses init `0x0000` or `0xFFFF`, so no amount
of searching byte ranges with a predefined algorithm list could ever have hit
it. `crack_checksum2.py` also required *all* frames in a length group to match,
so a single dropped-byte frame would have suppressed a correct answer anyway.

How it was actually found, for future reference -- the generalizable technique:
1. **Test the whole CRC family at once instead of guessing parameters.** For
   fixed-length messages, `crc(A) XOR crc(B)` depends only on `A XOR B`, and
   that map is GF(2)-linear, *for any poly/init/xorout*. Building
   `[data_diff | cksum_diff]` rows and row-reducing gave **0 linear
   contradictions across 1101 frames** (~1034 independent 16-bit checks) --
   proving it was a CRC before a single parameter was guessed.
2. **Recover the polynomial alone.** Because XOR-differencing cancels init and
   xorout entirely, brute-forcing only the 65536 polynomials against a few
   difference vectors uniquely yielded `0x1021`, MSB-first, little-endian.
   (Leading zero bytes of a difference vector don't affect an init=0 CRC, so
   differences can be stripped for speed.)
3. **Recover init/xorout from the residual.** `crc_init0(data) XOR stored` was
   constant within each length group but varied *between* lengths -- the
   signature of a non-zero init. Solving that across length groups gave
   `init=0x1021, xorout=0x0000`.

`flair_frame.py` implements this: `crc16()`, `check()`, `build(body)` and
`reseal(content)` (edit a captured frame, get a valid one back). Running it
directly re-verifies every log: `python3 flair_frame.py`.

**This unblocks forging arbitrary frames**, which in turn is what's needed to
test the position-byte hypothesis below by commanding positions the app never
exposes.

**Byte 28 of this command frame (0-indexed, i.e. the 29th content byte)
is the key finding**: confirmed **`0x64` (100 decimal) on 3 independent
close commands**, **`0x00` on 1 open command**, across multiple sessions
minutes/hours apart. Strong candidate for a target-position field (0-100
scale, "% closed" or similar internal convention -- not yet confirmed
which direction is 0 vs 100 in Flair's own semantics, just that it flips
cleanly between the two known states). Bytes at positions 20 and 25 also
differ between samples but look more like a sequence number/session
counter than a command-type field (differ between same-direction repeats
too).

**Not yet confirmed**: exact percentage encoding (app doesn't expose a
percentage slider; Home Assistant integration tested and also didn't
accept an arbitrary percent). **Now directly testable** -- with the CRC
cracked we can forge a command frame with byte 28 set to any intermediate
value (e.g. 0x32 = 50) and see whether the vent physically moves to a
partial position, which settles both the scale and its direction.
Note the bus-contention constraint: the ESP8266 must be held in reset or
disconnected before driving URX, per the replay-testing note above. Also
haven't captured the actual **over-the-air RF frame** that corresponds to
one of these UART command frames -- that's the next step, now with a
precise timing window (command frame appears -> RF TX happens within
~2-5s, confirmed by the following "TX succeeded" text) to aim a
YARD-Stick-One capture at instead of blind RSSI gating.

### Scripts added for this phase
- `flair_frame.py` -- the frame CRC + `build()`/`reseal()` frame forging.
  Run directly to re-verify every capture log.
- `decode_uart.py` -- parses a Saleae Logic 2 Async Serial CSV export
  (both channels) into text runs and `{...}` binary frames.
- `uart_monitor.py` -- live pyserial-based monitor for continued
  single-wire TTL adapter monitoring (same frame-parsing logic, real-time).
- `saleae_capture.py` -- drives the Logic 8 over Logic 2's automation API to
  capture BOTH UART directions at once, printing timed OPEN/CLOSE toggle cues
  and writing a cue sidecar so commands can be correlated afterwards.
- `cc430_drive.py` -- drives the CC430 directly from a 3.3V USB-TTL adapter
  with the ESP8266 held in reset, repeating the position command on the real
  ~6s cadence. Refuses to transmit until a listen-only pre-flight confirms the
  CC430 is alive and the ESP has actually gone quiet.
- `analyze_capture.py` -- decodes that export, identifies each channel's
  direction from the frame header (so wiring order doesn't matter), and diffs
  the frames following OPEN cues against those following CLOSE cues to find
  the command byte.

**Saleae tooling setup** (Logic 2.4.46, needs its own venv -- `grpcio` has no
Python 3.14 wheels, and 3.14 is the default `python3` on this machine):
```bash
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 -m venv .venv
.venv/bin/pip install logic2-automation
```
Logic 2 must have **Preferences -> Automation -> "Enable automation server"**
checked (gRPC on 127.0.0.1:10430). The setting lives in Electron local
storage, so it can only be flipped in the GUI.

Validated end to end against Logic 2's simulation device and a synthetic
capture: `analyze_capture.py` correctly identified both directions and
recovered a planted position byte at offset 28. Note it ranks candidate bytes
by scatter -- a byte holding one value per state is flagged STRONG, while a
sequence/counter byte that separates by luck is demoted. With only 2 samples
per state a random byte can separate by chance, so **run at least 6-8 toggles**.

## Standing constraints / preferences
- Prefer USB-connected solution (not ESP32-based) so it plugs directly into
  the Home Assistant host.
- Prefer not depending on a Puck in the final deployed system, even though
  one is being used temporarily to generate capture traffic.
- Development style: sniff protocol, minimal custom decode, build toward a
  clean native integration (consistent with prior ESPHome components built
  for Trane CAN bus, Balboa RS-485, and Navien RS-485 -- same reverse-
  engineer-then-build-clean-driver pattern).
