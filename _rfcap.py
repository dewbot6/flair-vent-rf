import time, json, sys, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rflib import RfCat, ChipconUsbTimeoutException
import rf_receive
d=RfCat(); rf_receive.configure(d)
out=[]; t0=time.time(); dur=float(sys.argv[1]) if len(sys.argv)>1 else 300
print(f"capturing {dur}s (full-length packets)", flush=True)
while time.time()-t0<dur:
    try: pkt,_=d.RFrecv(timeout=400)
    except ChipconUsbTimeoutException: continue
    if pkt:
        p=bytes(pkt); dt=round(time.time()-t0,2)
        out.append({"kind":"rf","dt":dt,"hex":p.hex()})
        json.dump(out, open("captures/rf_known_commands.json","w"))
        if len(p)>=20:
            print(f"  t={dt:6.1f}s len={p[0]:3d} ctr={p[19]:02x} {p[:20].hex(' ')}", flush=True)
d.setModeIDLE()
print(f"done, {len(out)} packets", flush=True)
