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

## Standing constraints / preferences
- Prefer USB-connected solution (not ESP32-based) so it plugs directly into
  the Home Assistant host.
- Prefer not depending on a Puck in the final deployed system, even though
  one is being used temporarily to generate capture traffic.
- Development style: sniff protocol, minimal custom decode, build toward a
  clean native integration (consistent with prior ESPHome components built
  for Trane CAN bus, Balboa RS-485, and Navien RS-485 -- same reverse-
  engineer-then-build-clean-driver pattern).
