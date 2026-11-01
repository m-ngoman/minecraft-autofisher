"""Dev test: run the real Fisher (real clicks, real guards) against the live Minecraft window.
Usage: real_test.py [seconds] [both|sound|subs]"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import autofish

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 90
MODE = sys.argv[2] if len(sys.argv) > 2 else "both"
hwnd, pid, title = next(w for w in autofish.list_windows() if w[2].startswith("Minecraft"))
fisher = autofish.Fisher()
fisher.start()
fisher.use_sound = MODE in ("both", "sound")
fisher.use_subs = MODE in ("both", "subs")
fisher.set_target(hwnd, pid)
print(f"target {title!r} pid {pid}  audio error: {fisher.audio_error}  mode {MODE}  threshold {fisher.threshold:.3f}", flush=True)
time.sleep(2)
t0 = time.time()
fisher.active = True


def sample_sound():
    peak, since = 0.0, time.time()
    while True:
        peak = max(peak, fisher.sound_prob)
        if time.time() - since > 0.25:
            if peak > 0.3:
                print(f"{time.time() - t0:6.1f}s   sound match {peak:.2f} (level {fisher.sound_db:.0f} dB)", flush=True)
            peak, since = 0.0, time.time()
        time.sleep(0.01)


threading.Thread(target=sample_sound, daemon=True).start()
last_event = last_status = ""
while time.time() - t0 < SECONDS:
    time.sleep(0.2)
    if fisher.status != last_status:
        last_status = fisher.status
        print(f"{time.time() - t0:6.1f}s status: {last_status}", flush=True)
    if fisher.last_event != last_event:
        last_event = fisher.last_event
        print(f"{time.time() - t0:6.1f}s EVENT: {last_event}", flush=True)
fisher.active = False
time.sleep(0.5)
fisher.stop_audio()
print(f"bites={fisher.bites} timeouts={fisher.timeouts} ignored={fisher.rejected}", flush=True)
