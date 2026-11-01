"""Dev check: render Minecraft subtitle lines with the game's own font and see if OCR + matching finds the splash line."""
import io
import os
import random
import sys
import zipfile

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fishsubs

JAR = os.path.join(os.environ["APPDATA"], "PrismLauncher", "libraries", "com", "mojang", "minecraft", "1.21.1",
                   "minecraft-1.21.1-client.jar")
atlas = Image.open(io.BytesIO(zipfile.ZipFile(JAR).read("assets/minecraft/textures/font/ascii.png"))).convert("RGBA")
atlas = np.array(atlas)
cell = atlas.shape[0] // 16


def glyph(ch):
    code = ord(ch)
    row, col = divmod(code, 16)
    g = atlas[row * cell:(row + 1) * cell, col * cell:(col + 1) * cell, 3] > 0
    cols = np.flatnonzero(g.any(axis=0))
    if ch == " " or not len(cols):
        return g[:, :0], 4 if ch == " " else 4
    return g[:, :cols[-1] + 1], cols[-1] + 2  # glyph pixels, advance (width + 1 px gap)


def text_mask(text):
    parts, width = [], 0
    for ch in text:
        g, adv = glyph(ch)
        parts.append((width, g)); width += adv
    mask = np.zeros((cell, max(width, 1)), bool)
    for x, g in parts:
        mask[:, x:x + g.shape[1]] |= g
    return mask


def scene(w, h, rng):
    """Noisy stand-in for a Minecraft frame: sky, grass, water, texture noise."""
    img = np.zeros((h, w, 3), np.float32)
    img[:h // 2] = (120, 170, 230)
    img[h // 2:] = (70, 130, 60)
    img[int(h * 0.7):, : w // 2] = (50, 90, 190)
    img += rng.normal(0, 18, img.shape)
    return np.clip(img, 0, 255)


def render(lines, gui_scale, brightness, w=1100, h=700, box_alpha=0.6, seed=0):
    """BGRA subtitle corner crop. lines[0] is the bottom-most line."""
    rng = np.random.default_rng(seed)
    img = scene(w, h, rng)
    s = gui_scale
    margin, row_h = 2 * s, 10 * s
    widths = [text_mask(t).shape[1] for t in lines]
    box_w = (max(widths) + 14) * s
    for i, text in enumerate(lines):
        y1 = h - (3 * s) - i * row_h
        y0 = y1 - 9 * s - 2 * s
        x1 = w - margin
        x0 = x1 - box_w
        img[y0:y1, x0:x1] *= (1 - box_alpha)
        mask = text_mask(text)
        mask = np.repeat(np.repeat(mask, s, axis=0), s, axis=1)
        tx = x0 + (box_w - mask.shape[1]) // 2
        ty = y0 + s * 2
        region = img[ty:ty + mask.shape[0], tx:tx + mask.shape[1]]
        region[mask] = brightness
    bgr = img[:, :, ::-1].astype(np.uint8)
    return np.dstack([bgr, np.full((h, w), 255, np.uint8)])


if __name__ == "__main__":
    reader = fishsubs.SubtitleReader()
    cases = [
        ("splash only", ["Fishing Bobber splashes"], True),
        ("splash + others", ["Footsteps", "Fishing Bobber splashes", "Bobber thrown"], True),
        ("arrows", ["< Fishing Bobber splashes >"], True),
        ("thrown only", ["Bobber thrown"], False),
        ("retrieved only", ["Bobber retrieved", "Footsteps"], False),
        ("generic splash", ["Splashing", "Footsteps"], False),
        ("nothing", [], False),
    ]
    ok = total = 0
    print("which image size finds the splash line (1 = as captured, -2 = half, 2 = double):")
    for gui_scale in (1, 2, 3, 4, 5, 6):
        row = []
        for brightness in (255, 160, 90):
            bgra = render(["Footsteps", "Fishing Bobber splashes"], gui_scale, brightness, w=1100 if gui_scale < 5 else 1400,
                          h=700 if gui_scale < 5 else 900)
            found = [f for f in fishsubs.SCALE_ORDER if fishsubs.find_splash_line(reader.read(bgra, f))]
            row.append(f"bright {brightness}: {found}")
        print(f"  GUI scale {gui_scale}: " + "   ".join(row))
    for gui_scale in (1, 2, 3, 4, 5, 6):
        for brightness in (255, 160, 90):
            for name, lines, expect in cases:
                big = gui_scale >= 5
                bgra = (render(lines, gui_scale, brightness, w=1400 if big else 1100, h=900 if big else 700) if lines
                        else render(["x"], gui_scale, 0, box_alpha=0))
                line, read = reader.find_splash(bgra)
                hit = line is not None
                total += 1; ok += hit == expect
                if hit != expect:
                    print(f"  MISS scale {gui_scale} bright {brightness:3d} [{name:15s}] expect {expect!s:5s} got {hit!s:5s} OCR: {read}")
    print(f"correct {ok}/{total}")
    Image.fromarray(render(["Footsteps", "Fishing Bobber splashes"], 3, 255)[:, :, [2, 1, 0]]).save(
        os.path.join(os.environ["TEMP"], "mock_subtitle.png"))
