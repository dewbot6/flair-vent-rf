"""Decode a 2-channel Saleae export and isolate the ESP8266 -> CC430 command.

Takes the CSV that saleae_capture.py produces, reassembles frames on both
channels, works out which channel is which direction from the frame header
(rather than trusting the wiring order), and then hunts for the byte that
tracks OPEN vs CLOSE.

The method: steady-state heartbeat frames repeat constantly, so a command
frame is distinguished by being rare AND landing just after a toggle cue. We
group the frames that follow OPEN cues against those that follow CLOSE cues
and report every byte position that separates the two groups cleanly.

Run with:  .venv/bin/python analyze_capture.py saleae_out/uart_*.csv
"""
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import flair_frame as ff

# ESP8266 = node 0x0001, CC430 = node 0x0000 (see CLAUDE.md)
NODE = {0x0001: "ESP8266", 0x0000: "CC430"}

# Seconds to ignore after a cue before trusting the state. Covers human
# reaction time to the prompt plus the cloud round trip -- measured at ~9s in
# the 2026-09-18 capture, so 15s leaves margin.
SETTLE = 15.0


def sniff_columns(fieldnames):
    """Logic 2's export column names have shifted between versions, so detect
    them rather than hard-coding one layout."""
    low = {f.lower().strip(): f for f in fieldnames}

    def pick(*cands):
        for c in cands:
            if c in low:
                return low[c]
        return None

    name = pick("name", "analyzer name", "analyzer")
    start = pick("start_time", "start time", "start")
    data = pick("data", "value", "mosi", "byte")
    err = pick("error", "framing_error", "is_error")
    typ = pick("type")
    missing = [n for n, v in (("name", name), ("start_time", start), ("data", data)) if v is None]
    if missing:
        raise SystemExit(f"Could not find column(s) {missing} in export. Header was: {fieldnames}")
    return name, start, data, err, typ


def load_channels(path):
    """-> {analyzer_name: [(time, byte), ...]}

    Rows carrying an analyzer error (the column holds a reason string such as
    "framing", not a boolean) are dropped -- a mis-sampled byte would corrupt
    framing and produce phantom CRC failures.
    """
    chans = defaultdict(list)
    dropped = 0
    with open(path) as f:
        reader = csv.DictReader(f)
        ncol, tcol, dcol, ecol, tycol = sniff_columns(reader.fieldnames)
        print(f"columns: name={ncol!r} time={tcol!r} data={dcol!r} error={ecol!r}")
        for r in reader:
            if tycol and r.get(tycol, "").strip().strip('"').lower() not in ("data", ""):
                continue
            if ecol and r.get(ecol, "").strip().strip('"').lower() not in ("", "none"):
                dropped += 1
                continue
            raw = r[dcol].strip().strip('"').lower()
            if raw.startswith("0x"):
                raw = raw[2:]
            try:
                b = int(raw, 16)
            except ValueError:
                continue
            chans[r[ncol].strip('"')].append((float(r[tcol]), b & 0xFF))
    if dropped:
        print(f"dropped {dropped} bytes with analyzer errors (framing/parity)")
    return chans


def extract_frames(stream):
    """Length-driven framing with CRC validation. Does NOT scan for 0x7D --
    the protocol doesn't escape delimiters, so a CRC byte of 0x7D would
    truncate the frame."""
    frames = []
    i, n = 0, len(stream)
    while i < n:
        if stream[i][1] == 0x7B and i + 1 < n:
            length = stream[i + 1][1]
            end = i + 1 + length
            if length >= 4 and end < n and stream[end][1] == 0x7D:
                content = bytes(b for _, b in stream[i + 1:end])
                if ff.check(content):
                    frames.append((stream[i][0], content))
                    i = end + 1
                    continue
        i += 1
    return frames


def describe(frames):
    src = Counter(f[6] | (f[7] << 8) for _, f in frames)
    dst = Counter(f[8] | (f[9] << 8) for _, f in frames)
    return src, dst


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    swap = "--swap" in sys.argv
    if not args:
        raise SystemExit("usage: analyze_capture.py [--swap] <export.csv>\n"
                         "  --swap  you performed the opposite action to each prompt")
    path = Path(args[0])
    if swap:
        print("NOTE: --swap given; OPEN/CLOSE cue labels are being inverted.")
    chans = load_channels(path)

    by_dir = {}
    for cname, stream in chans.items():
        stream.sort()
        frames = extract_frames(stream)
        src, _ = describe(frames)
        top_src = src.most_common(1)[0][0] if src else None
        label = NODE.get(top_src, f"unknown(0x{top_src:04X})" if top_src is not None else "no frames")
        print(f"\n[{cname}] {len(stream)} bytes -> {len(frames)} CRC-valid frames; "
              f"source = {label}")
        if frames:
            types = Counter((len(f), f[4]) for _, f in frames)
            for (L, t), n in types.most_common(8):
                print(f"    len={L:3d} type=0x{t:02X}  n={n}")
            by_dir.setdefault(label, []).extend(frames)

    esp = sorted(by_dir.get("ESP8266", []))
    if not esp:
        print("\nNo ESP8266->CC430 frames found. Check the channel wired to UTX.")
        return

    cue_path = path.with_name(path.stem + "_cues.json")
    if not cue_path.exists():
        print(f"\nNo cue file at {cue_path}; skipping OPEN/CLOSE correlation.")
        return
    meta = json.loads(cue_path.read_text())
    cues, duration = meta["cues"], meta.get("duration", 1e9)
    print(f"\nESP8266->CC430: {len(esp)} frames, {len(set(f.hex() for _, f in esp))} unique")

    # The command is desired STATE, not a one-shot event: the ESP re-sends it
    # every few seconds until it changes. So don't hunt for rare frames --
    # take every frame in the settled part of each cue's window and compare
    # value distributions between the OPEN windows and the CLOSE windows.
    windows = []
    for i, c in enumerate(cues):
        action = "OPEN" if "OPEN" in c["label"] else "CLOSE"
        if swap:
            action = "CLOSE" if action == "OPEN" else "OPEN"
        end = cues[i + 1]["t"] if i + 1 < len(cues) else duration
        windows.append((action, c["t"] + SETTLE, end))

    per_window = []
    for action, s, e in windows:
        fr = [f for t, f in esp if s <= t < e]
        per_window.append((action, s, e, fr))
        print(f"  window {s:6.1f}-{e:6.1f}s {action:5s}: {len(fr)} frames")
    if len({a for a, _, _, _ in per_window}) < 2:
        print("\nNeed both OPEN and CLOSE windows. Re-run with more toggles.")
        return

    # Score each byte by how well its per-window value tracks the cue state.
    # Scoring rather than demanding perfect separation matters because a cue
    # the vent ignored (offline, or already in that state) leaves a window
    # labelled OPEN/CLOSE whose traffic reflects the PREVIOUS state -- one
    # such window would otherwise veto the correct answer outright.
    # Family key includes the payload sub-header: the heartbeat and the command
    # frame share len=56/type=0x05 and differ only from byte 10 on, so keying on
    # (len, type) alone averages two unrelated messages together.
    def fam_key(f):
        return (len(f), f[4], bytes(f[10:14]))

    fams = sorted({fam_key(f) for _, _, _, fr in per_window for f in fr})
    scored = []
    for key in fams:
        L, typ = key[0], key[1]
        for i in range(L - 2):
            col = []
            for action, s, e, fr in per_window:
                sub = [f for f in fr if fam_key(f) == key]
                col.append((action, Counter(f[i] for f in sub).most_common(1)[0][0] if sub else None))
            present = [(a, v) for a, v in col if v is not None]
            if len({v for _, v in present}) < 2:
                continue
            mo = Counter(v for a, v in present if a == "OPEN").most_common(1)
            mc = Counter(v for a, v in present if a == "CLOSE").most_common(1)
            if not mo or not mc or mo[0][0] == mc[0][0]:
                continue
            maj = {"OPEN": mo[0][0], "CLOSE": mc[0][0]}
            score = sum(1 for a, v in present if v == maj[a])
            scored.append((score, len(present), L, typ, i, maj, col))

    print("\nCandidate command bytes (ranked; per-window value, * = disagrees):")
    if not scored:
        print("  none found.")
        return
    for score, n, L, typ, i, maj, col in sorted(scored, reverse=True)[:4]:
        print(f"\n  len={L} type=0x{typ:02X} byte[{i:2d}]  score {score}/{n}   "
              f"OPEN=0x{maj['OPEN']:02X}({maj['OPEN']})  CLOSE=0x{maj['CLOSE']:02X}({maj['CLOSE']})")
        cells = []
        for (action, v), (_, s, _, _) in zip(col, per_window):
            if v is None:
                cells.append(f"{s:5.0f}s {action[:1]}:--")
            else:
                cells.append(f"{s:5.0f}s {action[:1]}:0x{v:02X}{'' if v == maj[action] else '*'}")
        print("     " + "  ".join(cells))


if __name__ == "__main__":
    main()
