"""Dev end-to-end test: run the real Fisher against tools/fake_game.py. Usage: e2e_test.py [seconds] [sound|subs|both]"""
import ctypes
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import autofish

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 80
MODE = sys.argv[2] if len(sys.argv) > 2 else "both"
user32 = ctypes.windll.user32

CLICK_FILE = os.path.join(os.environ["TEMP"], "mcfish-click")
if os.path.exists(CLICK_FILE):
    os.remove(CLICK_FILE)
game = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "fake_game.py"), str(SECONDS), CLICK_FILE],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
log = []


def reader():
    for line in game.stdout:
        log.append(line.rstrip())
        print("  game |", line.rstrip(), flush=True)


threading.Thread(target=reader, daemon=True).start()

target = None
for _ in range(60):
    time.sleep(0.5)
    found = [w for w in autofish.list_windows() if "fake test world" in w[2]]
    if found:
        target = found[0]
        break
assert target, "fake game window never appeared:\n" + "\n".join(log)
hwnd, pid, title = target
time.sleep(1.5)

# Non-intrusive: no focus stealing and no real mouse. The click is posted straight to the fake window,
# and the focus/mouse guard is bypassed (it is exercised separately). Everything else is the real code path.
def posted_click():
    open(CLICK_FILE, "w").close()
    time.sleep(0.1)


autofish.right_click = posted_click
autofish.not_ready = lambda hwnd: None

# the real SendInput structure: a zero-distance mouse move must be accepted (returns 1 event) and clicks nothing
assert ctypes.sizeof(autofish.INPUT) == 40, ctypes.sizeof(autofish.INPUT)
probe = autofish.INPUT(type=0, mi=autofish.MOUSEINPUT(0, 0, 0, 0x0001, 0, 0))
assert user32.SendInput(1, ctypes.byref(probe), ctypes.sizeof(autofish.INPUT)) == 1
print("SendInput struct OK", flush=True)

fisher = autofish.Fisher()
fisher.start()
fisher.use_sound = MODE in ("sound", "both")
fisher.use_subs = MODE in ("subs", "both")
fisher.set_target(hwnd, pid)
print(f"audio capture error: {fisher.audio_error}  mode: {MODE}  threshold {fisher.threshold}", flush=True)
time.sleep(2.5)
fisher.active = True

last_event, last_status, started = "", "", time.time()
while game.poll() is None:
    time.sleep(0.25)
    if fisher.last_event != last_event:
        last_event = fisher.last_event
        print(f"  fisher | {last_event}", flush=True)
    if fisher.status != last_status:
        last_status = fisher.status
        print(f"  status | {time.time() - started:5.1f}s {last_status}  (sound {fisher.sound_prob:.2f}, {fisher.sound_db:.0f} dB)", flush=True)
fisher.active = False
fisher.stop_audio()
time.sleep(0.5)
print(f"fisher counters: bites={fisher.bites} timeouts={fisher.timeouts} ignored={fisher.rejected}  status={fisher.status}")
