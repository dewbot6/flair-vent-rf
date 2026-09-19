"""Capture UART and RF simultaneously, timestamped, and correlate them.

With the ESP8266 reconnected the Puck runs normally, so this sees traffic the
isolated CC430 never produces: cloud-initiated commands, pairing, and the
vent's own replies.

The point is the correlation. The UART frame carrying a position command is
plaintext and fully understood; the RF body that follows it is not. Capturing
both with timestamps lets us line up "this exact UART payload" against "this
exact RF packet" and work out the transformation between them -- which is the
open question blocking RF transmit (see CLAUDE.md: the 0x00/0x64 position byte
is visible in UART but not in the RF body).

WIRING -- the ESP8266 is live, so the UART tap MUST be listen-only:
    TTL RX  -> the line the CC430 transmits on
    TTL GND -> Puck GND
    TTL TX  -> NOT CONNECTED. Driving a live bus contends with the ESP8266.

Run with:  python3 dual_capture.py <uart_port> [--seconds 300]
"""
import argparse
import json
import threading
import time

import serial
from rflib import RfCat, ChipconUsbTimeoutException

import cc430_drive as cd
import rf_receive as rr

ADDRS = {
    bytes.fromhex("00124b00380d16fd"): "TI-OUI",
    bytes.fromhex("46819ee3001d000f"): "other",
    b"\xff" * 8: "broadcast",
}


def uart_thread(port, events, stop):
    try:
        ser = serial.Serial(port, 115200, timeout=0.2)
    except Exception as e:
        print(f"UART open failed: {e}")
        return
    buf = bytearray()
    while not stop.is_set():
        c = ser.read(256)
        if not c:
            continue
        buf.extend(c)
        frames, buf = cd.frames_from(buf)
        for f in frames:
            src = f[6] | (f[7] << 8)
            events.append({
                "t": time.time(), "kind": "uart",
                "src": "ESP" if src == 1 else "CC430" if src == 0 else hex(src),
                "type": f[4], "len": len(f), "hex": f.hex(),
            })
    ser.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("port")
    p.add_argument("--seconds", type=float, default=300.0)
    p.add_argument("--out", default="dual_capture.json")
    args = p.parse_args()

    events = []
    stop = threading.Event()
    t = threading.Thread(target=uart_thread, args=(args.port, events, stop), daemon=True)
    t.start()

    d = RfCat()
    rr.configure(d)
    print(f"UART + RF capture for {args.seconds:.0f}s. Use the Flair app to "
          "open/close the vent a few times.\n")

    end = time.time() + args.seconds
    n_rf = n_uart_seen = 0
    try:
        while time.time() < end:
            try:
                pkt, _ = d.RFrecv(timeout=300)
            except ChipconUsbTimeoutException:
                pkt = None
            if pkt:
                pkt = bytes(pkt)
                a1, a2 = pkt[1:9], pkt[9:17]
                events.append({"t": time.time(), "kind": "rf", "len": pkt[0],
                               "a1": ADDRS.get(a1, a1.hex()),
                               "a2": ADDRS.get(a2, a2.hex()), "hex": pkt.hex()})
                n_rf += 1
                print(f"  [RF  ] len={pkt[0]:3d} {ADDRS.get(a1,a1.hex())[:10]:10s}"
                      f"-> {ADDRS.get(a2,a2.hex())[:10]:10s} {pkt[17:30].hex(' ')}", flush=True)
            if len(events) - n_rf > n_uart_seen:
                for e in events[n_uart_seen + n_rf:]:
                    if e["kind"] == "uart":
                        print(f"  [UART] {e['src']:6s} type=0x{e['type']:02X} len={e['len']}",
                              flush=True)
                n_uart_seen = sum(1 for e in events if e["kind"] == "uart")
    finally:
        stop.set()
        d.setModeIDLE()

    events.sort(key=lambda e: e["t"])
    t0 = events[0]["t"] if events else 0
    for e in events:
        e["dt"] = round(e["t"] - t0, 4)
    json.dump(events, open(args.out, "w"), indent=1)
    nu = sum(1 for e in events if e["kind"] == "uart")
    print(f"\n{nu} UART frames, {n_rf} RF packets -> {args.out}")

    # pair each RF packet with the UART frames just before it
    print("\ncorrelation (RF packet <- UART frames within the preceding 15s):")
    for i, e in enumerate(events):
        if e["kind"] != "rf":
            continue
        near = [x for x in events[:i]
                if x["kind"] == "uart" and 0 <= e["dt"] - x["dt"] <= 15]
        tags = [f"{x['src']}/0x{x['type']:02X}(+{e['dt']-x['dt']:.1f}s)" for x in near[-4:]]
        print(f"  RF t={e['dt']:7.1f}s len={e['len']:3d} {e['a1'][:9]:9s}->{e['a2'][:9]:9s}"
              f"  after: {', '.join(tags) or 'nothing'}")


if __name__ == "__main__":
    main()
