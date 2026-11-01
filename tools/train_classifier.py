"""Dev tool: train per-pitch logistic classifiers that recognise the splash onset, test on held-out sounds.

Usage: train_classifier.py [--export]
With --export, writes splash_model.py (weights embedded as base64) for the app to import.
"""
import os
import random
import sys
import time

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from sklearn.linear_model import LogisticRegression

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import assets
import fishsound as fs

SR = fs.SR
SECONDS = 0.4
rng = np.random.default_rng(3)
random.seed(3)
SPLASH = "minecraft/sounds/random/splash.ogg"
objects = assets.all_sounds()
splash = assets.load_mono48k(objects[SPLASH])
others = sorted(n for n in objects if n != SPLASH)
random.shuffle(others)
train_files, test_files = others[::2], others[1::2]
cache = {}


def sound(name):
    if name not in cache:
        cache[name] = assets.load_mono48k(objects[name])
    return cache[name]


def game_pitch():
    return 1.0 + (random.random() - random.random()) * 0.4


def norm_peak(x, level):
    return x / (np.abs(x).max() + 1e-9) * level


def quiet(seconds):
    return (rng.standard_normal(int(seconds * SR)) * 1e-4).astype(np.float32)


def feats_of(x):
    return fs.log_bands(x)


def vec(window):
    w = window - window.mean()
    return (w / (w.std() + 1e-6)).ravel()


LEAD = 1.2
LEAD_FRAMES = int(LEAD * SR) // fs.HOP


def render_positive(pitch, files, with_bg):
    x = norm_peak(fs.repitch(splash, pitch), random.uniform(0.05, 0.6))
    sig = np.zeros(int((LEAD + 1.0) * SR) + len(x), np.float32)
    sig[int(LEAD * SR):int(LEAD * SR) + len(x)] += x
    if with_bg:
        bg = norm_peak(fs.repitch(sound(random.choice(files)), game_pitch()), random.uniform(0.02, 0.4))
        offset = random.randint(0, 20000)
        seg = bg[:len(sig) - offset]
        sig[offset:offset + len(seg)] += seg
    sig += (rng.standard_normal(len(sig)) * 1e-4).astype(np.float32)
    return feats_of(sig)


# window length (frames) per pitch bucket
N = {p: len(fs.log_bands(fs.repitch(splash[:int(SECONDS * SR)], p))) for p in fs.PITCHES}
print("window frames:", N)


def build_training(files, n_pos=500, neg_per_file=6):
    data = {p: ([], []) for p in fs.PITCHES}
    for p in fs.PITCHES:
        n = N[p]
        for k in range(n_pos):
            f = render_positive(p + random.uniform(-0.05, 0.05), files, with_bg=(k % 2 == 1))
            end = LEAD_FRAMES + n - 1 + random.randint(-2, 2)
            data[p][0].append(vec(f[end + 1 - n:end + 1]))
            # misaligned splash windows (earlier/later parts of the same sound) are negatives
            shift = random.choice([-1, 1]) * random.randint(6, 80)
            end2 = LEAD_FRAMES + n - 1 + shift
            if n <= end2 + 1 <= len(f):
                data[p][1].append(vec(f[end2 + 1 - n:end2 + 1]))
    for name in files:
        x = norm_peak(fs.repitch(sound(name), game_pitch())[:SR * 4], random.uniform(0.05, 0.6))
        f = feats_of(np.concatenate([quiet(0.8), x, quiet(0.4)]))
        for p in fs.PITCHES:
            n = N[p]
            if len(f) <= n:
                continue
            for end in np.random.default_rng(hash(name) % 2**32).integers(n - 1, len(f), neg_per_file):
                data[p][1].append(vec(f[end + 1 - n:end + 1]))
    return data


t0 = time.time()
data = build_training(train_files)
print(f"training data built {time.time()-t0:.0f}s:", {p: (len(a), len(b)) for p, (a, b) in data.items()})
models = {}
for p, (pos, neg) in data.items():
    X = np.array(pos + neg, np.float32)
    y = np.array([1] * len(pos) + [0] * len(neg))
    clf = LogisticRegression(C=0.05, max_iter=400, class_weight="balanced")
    clf.fit(X, y)
    models[p] = (clf.coef_[0].astype(np.float32), float(clf.intercept_[0]))
print(f"trained {time.time()-t0:.0f}s")


def stream_scores(f):
    """max probability over all windows and pitches in a spectrogram."""
    best = 0.0
    for p, (w, b) in models.items():
        n = N[p]
        if len(f) < n:
            continue
        wins = sliding_window_view(f, n, axis=0).transpose(0, 2, 1)
        flat = wins.reshape(len(wins), -1)
        flat = flat - flat.mean(axis=1, keepdims=True)
        flat = flat / (flat.std(axis=1, keepdims=True) + 1e-6)
        z = flat @ w + b
        best = max(best, float((1 / (1 + np.exp(-z))).max()))
    return best


def padded(x):
    return np.concatenate([quiet(1.2), x, quiet(0.6)])


pos_alone, pos_bg, neg = [], [], []
for _ in range(150):
    pos_alone.append(stream_scores(feats_of(padded(norm_peak(fs.repitch(splash, game_pitch()), random.uniform(0.05, 0.6))))))
for _ in range(150):
    x = norm_peak(fs.repitch(splash, game_pitch()), random.uniform(0.05, 0.6))
    bg = norm_peak(fs.repitch(sound(random.choice(test_files)), game_pitch()), random.uniform(0.02, 0.4))[:SR * 4]
    off = random.randint(0, 4000)
    mix = np.zeros(max(len(bg), off + len(x)), np.float32)
    mix[:len(bg)] += bg
    mix[off:off + len(x)] += x
    pos_bg.append(stream_scores(feats_of(padded(mix))))
worst = []
for name in test_files:
    x = norm_peak(fs.repitch(sound(name), game_pitch())[:SR * 4], random.uniform(0.05, 0.6))
    s = stream_scores(feats_of(padded(x)))
    neg.append(s)
    worst.append((s, name))
pos_alone, pos_bg, neg = map(np.array, (pos_alone, pos_bg, neg))
print(f"evaluated {time.time()-t0:.0f}s on {len(neg)} held-out sounds")
print("threshold  TPR(alone)  TPR(+bg)  FPR(held-out others)")
for t in (0.5, 0.7, 0.8, 0.9, 0.95, 0.98, 0.99, 0.995):
    print(f"  {t:.3f}     {np.mean(pos_alone>=t):.2f}        {np.mean(pos_bg>=t):.2f}      {np.mean(neg>=t):.3f}")
print("worst offenders:")
for s, name in sorted(worst, reverse=True)[:10]:
    print(f"  {s:.3f}  {name.replace('minecraft/sounds/','')}")

if "--export" in sys.argv:
    import base64, io
    buf = io.BytesIO()
    np.savez_compressed(buf, pitches=np.array(fs.PITCHES, np.float32),
                        **{f"w{i}": models[p][0].astype(np.float16) for i, p in enumerate(fs.PITCHES)},
                        b=np.array([models[p][1] for p in fs.PITCHES], np.float32),
                        n=np.array([N[p] for p in fs.PITCHES], np.int32))
    out = os.path.join(os.path.dirname(__file__), "..", "splash_model.py")
    with open(out, "w") as fh:
        fh.write('"""Generated by tools/train_classifier.py - splash onset classifier weights."""\n')
        fh.write(f'DATA = "{base64.b64encode(buf.getvalue()).decode()}"\n')
    print("exported", os.path.abspath(out))
