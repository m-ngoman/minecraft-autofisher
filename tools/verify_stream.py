"""Dev check: run the real streaming SplashMatcher on held-out game sounds, grouped by what you'd hear while fishing."""
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import assets
import fishsound as fs
from splash_model import DATA

THRESHOLD = float(sys.argv[1]) if len(sys.argv) > 1 else 0.95
model = fs.load_model(DATA)

# same split as train_classifier.py
rng = np.random.default_rng(3)
random.seed(3)
SPLASH = "minecraft/sounds/random/splash.ogg"
objects = assets.all_sounds()
splash = assets.load_mono48k(objects[SPLASH])
others = sorted(n for n in objects if n != SPLASH)
random.shuffle(others)
test_files = others[1::2]


def game_pitch():
    return 1.0 + (random.random() - random.random()) * 0.4


def norm_peak(x, level):
    return x / (np.abs(x).max() + 1e-9) * level


def run(signal, onset_sample=None):
    matcher = fs.SplashMatcher(model)
    lead = (rng.standard_normal(int(1.2 * fs.SR)) * 1e-4).astype(np.float32)
    padded = np.concatenate([lead, signal, np.zeros(int(0.5 * fs.SR), np.float32)])
    best, first_hit = 0.0, None
    for start in range(0, len(padded), 960):  # 20 ms chunks like the live capture
        prob, _, _ = matcher.feed(padded[start:start + 960])
        best = max(best, prob)
        if first_hit is None and prob >= THRESHOLD:
            first_hit = (start + 960) / fs.SR - 1.2
    return best, first_hit


groups = {
    "footsteps (step/)": "/step/", "chest/door/click": ("block/chest", "door", "random/click", "random/pop", "random/bow"),
    "player": "entity/player/", "water/liquid": "liquid/", "rain/weather": "weather/", "fishing bobber": "bobber/",
    "block sounds": "/block/", "mobs": "/mob/", "ambient": "ambient/",
}
print(f"threshold {THRESHOLD}")
print(f"{'group':22s} {'sounds':>6s} {'false hits':>10s}")
for label, key in groups.items():
    keys = key if isinstance(key, tuple) else (key,)
    names = [n for n in test_files if any(k in n for k in keys)]
    hits, shown = 0, []
    for name in names:
        x = norm_peak(fs.repitch(assets.load_mono48k(objects[name]), game_pitch())[:fs.SR * 4], random.uniform(0.05, 0.6))
        if run(x)[0] >= THRESHOLD:
            hits += 1
            shown.append(name.replace("minecraft/sounds/", ""))
    print(f"{label:22s} {len(names):6d} {hits:10d}  {shown[:3] if shown else ''}")

delays, found = [], 0
for _ in range(100):
    p = game_pitch()
    best, hit = run(norm_peak(fs.repitch(splash, p), random.uniform(0.05, 0.6)))
    if hit is not None:
        found += 1
        delays.append(hit)
print(f"splash detected in {found}/100 random pitch/volume trials; "
      f"time from sound start to detection: median {np.median(delays)*1000:.0f} ms, worst {max(delays)*1000:.0f} ms")
