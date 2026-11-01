"""Dev tool: cast once in the real game, then log every caption line the OCR reads. Usage: watch_captions.py [seconds]"""
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import autofish
import fishsubs
import mss

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 40
hwnd = next(w[0] for w in autofish.list_windows() if w[2].startswith("Minecraft"))
reader = fishsubs.SubtitleReader()
t0 = time.time()
print("casting", flush=True)
autofish.right_click()
last = None
saved = 0
with mss.MSS() as sct:
    while time.time() - t0 < SECONDS:
        image = fishsubs.grab(*fishsubs.client_region(hwnd), sct)
        seen = {}
        for factor in fishsubs.SCALE_ORDER:
            for line in reader.read(image, factor):
                seen.setdefault(line.strip(), factor)
        lines = sorted(seen)
        if lines != last:
            print(f"{time.time() - t0:5.1f}s  {[(l, seen[l]) for l in lines]}  splash-line={fishsubs.find_splash_line(lines)!r}", flush=True)
            if saved < 6 and lines:
                autofish.save_png(os.path.join(os.environ["TEMP"], "mcshots", f"caption_{saved}.png"), image)
                saved += 1
            last = lines
        time.sleep(0.4)
print("reeling in", flush=True)
autofish.right_click()
