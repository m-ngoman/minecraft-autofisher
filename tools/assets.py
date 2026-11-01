"""Dev helper: load Minecraft sound assets from the local Prism launcher install."""
import json
import os

import numpy as np
import soundfile as sf

ROOT = os.path.join(os.environ["APPDATA"], "PrismLauncher", "assets")
SR = 48000


def index(name="34.json"):
    with open(os.path.join(ROOT, "indexes", name)) as f:
        return json.load(f)["objects"]


def load_mono48k(entry):
    path = os.path.join(ROOT, "objects", entry["hash"][:2], entry["hash"])
    data, rate = sf.read(path, dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if rate != SR:
        positions = np.arange(0, len(mono) - 1, rate / SR)
        mono = np.interp(positions, np.arange(len(mono)), mono).astype(np.float32)
    return mono


def all_sounds(skip=("music/", "records/")):
    objects = index()
    return {name: entry for name, entry in objects.items()
            if name.startswith("minecraft/sounds/") and name.endswith(".ogg")
            and not any(s in name for s in skip)}
