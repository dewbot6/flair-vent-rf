"""Drive the Saleae Logic 8 to capture BOTH UART directions at once.

Why both channels: every capture so far sniffed a single wire, so we have 4751
frames of CC430->ESP8266 and only 168 of ESP8266->CC430 -- and none of the
latter contain a position command. Capturing both simultaneously gives
request/response pairing with timestamps, so an ESP command and the CC430's
reply (position byte + "TX succeeded") land in the same window.

Wiring (all referenced to Puck GND):
    Logic CH0 -> UTX   (ESP8266 transmit -> CC430)   frames with dir byte 0x01
    Logic CH1 -> URX   (CC430 transmit -> ESP8266)   frames with dir byte 0x11
    Logic GND -> Puck GND
Channel order doesn't actually matter -- analyze_capture.py identifies each
direction from the frame header, so a swap is detected rather than assumed.

Logic 2 must be running with the automation server enabled:
    Logic 2 -> Preferences -> Automation -> "Enable automation server"

Run with:  .venv/bin/python saleae_capture.py [--duration 420] [--toggles 8]
"""
import argparse
import json
import threading
import time
from pathlib import Path

from saleae import automation

ASYNC_SERIAL_SETTINGS = {
    "Bit Rate (Bits/s)": 115200,
    "Bits per Frame": "8 Bits per Transfer (Standard)",
    "Stop Bits": "1 Stop Bit (Standard)",
    "Parity Bit": "No Parity Bit (Standard)",
    "Significant Bit": "Least Significant Bit Sent First (Standard)",
    "Signal inversion": "Non Inverted (Standard)",
    "Mode": "Normal",
}


def cue_thread(cues, stop_event):
    """Print toggle prompts on schedule so the capture has known-good markers."""
    start = time.time()
    for at, label in cues:
        delay = at - (time.time() - start)
        if delay > 0 and stop_event.wait(delay):
            return
        print(f"\n  >>> [t={at:5.0f}s] {label}", flush=True)


def build_cues(duration, toggles, lead_in, tail):
    """Alternating OPEN/CLOSE cues spread across the capture window."""
    usable = duration - lead_in - tail
    if toggles < 1 or usable <= 0:
        return []
    spacing = usable / toggles
    cues = []
    for i in range(toggles):
        action = "OPEN" if i % 2 == 0 else "CLOSE"
        cues.append((lead_in + i * spacing, f"TOGGLE #{i+1}: set vent {action}  (then leave it alone)"))
    return cues


def export_tables(capture, csv_path, channels):
    """Attach one Async Serial analyzer per channel and export the data table.

    Paths must be absolute -- see the note in main(). Kept separate from the
    capture itself so it can be re-run against a saved .sal.
    """
    csv_path = Path(csv_path).expanduser().resolve()
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    analyzers = [
        capture.add_analyzer("Async Serial", label=f"ch{ch}",
                             settings={**ASYNC_SERIAL_SETTINGS, "Input Channel": ch})
        for ch in channels
    ]
    capture.export_data_table(
        filepath=str(csv_path),
        analyzers=[automation.DataTableExportConfiguration(a, automation.RadixType.HEXADECIMAL)
                   for a in analyzers],
    )
    return csv_path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--export-only", metavar="CAPTURE.sal",
                   help="skip capturing: load a saved .sal and export its data table")
    p.add_argument("--duration", type=float, default=420.0, help="capture seconds")
    p.add_argument("--toggles", type=int, default=8, help="number of toggle cues")
    p.add_argument("--lead-in", type=float, default=30.0, help="quiet seconds before first cue")
    p.add_argument("--tail", type=float, default=40.0, help="quiet seconds after last cue")
    p.add_argument("--sample-rate", type=int, default=2_000_000)
    p.add_argument("--channels", type=int, nargs=2, default=[0, 1])
    p.add_argument("--outdir", default="saleae_out")
    p.add_argument("--sim", action="store_true", help="use Logic 2's simulation device (pipeline test, no hardware)")
    p.add_argument("--no-prompt", action="store_true", help="don't wait for Enter before capturing")
    p.add_argument("--launch", action="store_true", help="launch Logic 2 instead of connecting to a running instance")
    args = p.parse_args()

    # MUST be absolute: Logic 2 is a separate process with its own working
    # directory, so a relative path resolves somewhere else entirely and the
    # export fails with an opaque "ios_base::clear" iostream error.
    outdir = Path(args.outdir).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")

    manager = automation.Manager.launch() if args.launch else automation.Manager.connect()

    if args.export_only:
        sal = Path(args.export_only).expanduser().resolve()
        with manager:
            capture = manager.load_capture(str(sal))
            csv_path = export_tables(capture, sal.with_suffix(".csv"), args.channels)
            capture.close()
        print(f"\n  CSV  {csv_path}")
        print(f"\nNext:  .venv/bin/python analyze_capture.py {csv_path}")
        return

    with manager:
        devices = manager.get_devices(include_simulation_devices=args.sim)
        if not devices:
            raise SystemExit("No Logic device found. Plug in the Logic 8 (or pass --sim).")
        if args.sim:
            devices = [d for d in devices if d.is_simulation] or devices
        else:
            real = [d for d in devices if not d.is_simulation]
            if not real:
                raise SystemExit("Only simulation devices present -- is the Logic 8 plugged in?")
            devices = real
        dev = devices[0]
        print(f"Device: {dev.device_type} id={dev.device_id} simulation={dev.is_simulation}")

        device_config = automation.LogicDeviceConfiguration(
            enabled_digital_channels=list(args.channels),
            digital_sample_rate=args.sample_rate,
        )
        capture_config = automation.CaptureConfiguration(
            buffer_size_megabytes=1024,
            capture_mode=automation.TimedCaptureMode(duration_seconds=args.duration),
        )

        cues = build_cues(args.duration, args.toggles, args.lead_in, args.tail)
        print(f"\nCapturing {args.duration:.0f}s at {args.sample_rate/1e6:.1f} MS/s "
              f"on channels {args.channels}.")
        print(f"{len(cues)} toggle cues; first at t={args.lead_in:.0f}s. "
              "Toggle from the Flair app when prompted.\n")

        # Pin the starting state: if the vent is already in the state the first
        # cue asks for, that command is a no-op and the sample is lost.
        if cues and not args.no_prompt:
            first = "OPEN" if "OPEN" in cues[0][1] else "CLOSE"
            opposite = "CLOSED" if first == "OPEN" else "OPEN"
            print(f"  Set the vent {opposite} now and let it finish moving,")
            print(f"  so the first cue ({first}) is a real state change.")
            input("  Press Enter to start the capture... ")

        capture = manager.start_capture(
            device_id=dev.device_id,
            device_configuration=device_config,
            capture_configuration=capture_config,
        )
        stop_event = threading.Event()
        t = threading.Thread(target=cue_thread, args=(cues, stop_event), daemon=True)
        t.start()
        try:
            capture.wait()
        finally:
            stop_event.set()

        print("\nCapture complete.")
        # Save the raw capture BEFORE anything else can fail. Analyzers and
        # exports are re-runnable from a .sal; an unsaved capture is not.
        sal_path = outdir / f"uart_{stamp}.sal"
        capture.save_capture(filepath=str(sal_path))
        print(f"  saved {sal_path}")

        cues_path = outdir / f"uart_{stamp}_cues.json"
        cues_path.write_text(json.dumps(
            {"duration": args.duration, "channels": args.channels,
             "cues": [{"t": at, "label": lbl} for at, lbl in cues]}, indent=2))

        csv_path = export_tables(capture, outdir / f"uart_{stamp}.csv", args.channels)
        capture.close()

        print(f"\n  CSV   {csv_path}")
        print(f"  .sal  {sal_path}")
        print(f"\nNext:  .venv/bin/python analyze_capture.py {csv_path}")


if __name__ == "__main__":
    main()
