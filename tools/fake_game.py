"""Dev test double: a window that behaves like Minecraft fishing, for testing autofish end to end.

Plays the real splash/footstep sounds, draws subtitles with the game's real font in the bottom-right
corner, and simulates cast -> bite (fish escapes after 1-2 s) -> reel. Also fires decoys that must
NOT be reeled in. Prints one event per line; usage: fake_game.py <seconds>
"""
import ctypes
import os
import random
import sys
import tempfile
import time
import tkinter as tk
import winsound

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import assets
import fishsound as fs
import test_subtitles as ts

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 90
W, H, GUI_SCALE = 900, 560, 3
T0 = time.time()
random.seed()


def log(msg):
    print(f"{time.time() - T0:6.1f} {msg}", flush=True)


# -- sounds: real game audio written as quiet wav files
workdir = tempfile.mkdtemp(prefix="mcfish-fake-")
objects = assets.all_sounds()
splash = assets.load_mono48k(objects["minecraft/sounds/random/splash.ogg"])
step_name = next(n for n in sorted(objects) if "/step/grass" in n)
step = assets.load_mono48k(objects[step_name])


def write_wav(name, mono, peak=0.2):
    path = os.path.join(workdir, name)
    sf.write(path, mono / (np.abs(mono).max() + 1e-9) * peak, fs.SR, subtype="PCM_16")
    return path


splash_wavs = {p: write_wav(f"splash_{p}.wav", fs.repitch(splash, p)) for p in (0.7, 0.85, 1.0, 1.15, 1.3)}
step_wav = write_wav("step.wav", step, peak=0.2)


def play(path):
    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)


# -- window
root = tk.Tk()
root.title("Minecraft 1.21.1 - fake test world")
root.geometry(f"{W}x{H}+40+40")
root.attributes("-topmost", True)
canvas = tk.Canvas(root, width=W, height=H, highlightthickness=0, bd=0)
canvas.pack()
image_item = canvas.create_image(0, 0, anchor="nw")
photo = None
subtitles = []  # [text, born]


def redraw():
    global photo
    now = time.time()
    subtitles[:] = [s for s in subtitles if now - s[1] < 3.0]
    if subtitles:
        newest_age = now - max(s[1] for s in subtitles)
        brightness = int(255 + (75 - 255) * min(1.0, newest_age / 3.0))
        lines = [s[0] for s in sorted(subtitles, key=lambda s: -s[1])]
        bgra = ts.render(lines, GUI_SCALE, brightness, w=W, h=H, seed=5)
    else:
        bgra = ts.render(["x"], GUI_SCALE, 0, w=W, h=H, box_alpha=0, seed=5)
    rgb = np.ascontiguousarray(bgra[:, :, [2, 1, 0]])
    photo = tk.PhotoImage(data=b"P6 %d %d 255\n" % (W, H) + rgb.tobytes())
    canvas.itemconfig(image_item, image=photo)


def say(text):
    subtitles.append([text, time.time()])
    redraw()


# -- fishing state machine
state = "idle"  # idle (rod in hand) / cast (bobber waiting) / bite (fish on, escapes soon)
timers = {}
stats = {"cast": 0, "bite": 0, "catch": 0, "escape": 0, "empty_reel": 0, "decoy": 0}


def cancel(key):
    if key in timers:
        root.after_cancel(timers.pop(key))


def schedule_bite():
    cancel("bite")
    timers["bite"] = root.after(int(random.uniform(3, 6) * 1000), do_bite)


def do_bite():
    global state
    state = "bite"
    pitch = random.choice(list(splash_wavs))
    stats["bite"] += 1
    log(f"BITE (splash sound pitch {pitch} + subtitle)")
    play(splash_wavs[pitch])
    say("Fishing Bobber splashes")
    timers["escape"] = root.after(int(random.uniform(1.0, 2.0) * 1000), escape)


def escape():
    global state
    stats["escape"] += 1
    log("ESCAPE (fish got away: bite was missed)")
    state = "cast"
    schedule_bite()


def on_click(_event):
    global state
    if state == "idle":
        state = "cast"
        stats["cast"] += 1
        log("CAST")
        say("Bobber thrown")
        schedule_bite()
    elif state == "cast":
        stats["empty_reel"] += 1
        log("EMPTY_REEL (reeled in with no fish: false trigger or timeout)")
        cancel("bite")
        state = "idle"
        say("Bobber retrieved")
    else:
        stats["catch"] += 1
        log("CATCH")
        cancel("escape")
        state = "idle"
        say("Bobber retrieved")


def decoy():
    if state == "cast":
        kind = random.choice(["footsteps", "sound_only", "subtitle_only"])
        stats["decoy"] += 1
        log(f"DECOY {kind}")
        if kind == "footsteps":
            play(step_wav); say("Footsteps")
        elif kind == "sound_only":
            play(splash_wavs[random.choice(list(splash_wavs))])
        else:
            say("Fishing Bobber splashes")
    root.after(int(random.uniform(6, 10) * 1000), decoy)


CLICK_FILE = sys.argv[2] if len(sys.argv) > 2 else None


def poll_click():
    if CLICK_FILE and os.path.exists(CLICK_FILE):
        os.remove(CLICK_FILE)
        on_click(None)
    root.after(30, poll_click)


def tick():
    if time.time() - T0 > DURATION:
        log(f"DONE {stats}")
        root.destroy()
        return
    if subtitles:
        redraw()
    root.after(250, tick)


root.bind("<Button-3>", on_click)
root.after(30, poll_click)
redraw()
root.update()
log(f"READY pid {os.getpid()} window {root.winfo_id()}")
root.after(250, tick)
root.after(7000, decoy)
root.mainloop()
