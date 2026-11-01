"""Minecraft (Java) auto-fisher for Windows. External: nothing is injected into the game.

A bite is only accepted when BOTH checks agree (each can be switched off):
  * sound:     the game's own audio contains the bobber-splash sound
               (per-process audio capture + a classifier trained on the real game sounds)
  * subtitles: the on-screen subtitle reads "Fishing Bobber splashes"
               (screenshot of the window corner + Windows' built-in OCR)
Then it right-clicks to reel in, and again to recast.

Run:    python autofish.py
Needs:  pip install proc-tap mss numpy winrt-runtime winrt-Windows.Media.Ocr winrt-Windows.Graphics.Imaging
        winrt-Windows.Storage.Streams winrt-Windows.Foundation winrt-Windows.Foundation.Collections
        winrt-Windows.Globalization
Build:  see build.ps1
"""
import ctypes
import os
import random
import re
import struct
import sys
import threading
import time
import tkinter as tk
import zlib
from ctypes import wintypes
from tkinter import ttk

import numpy as np

import fishsound
import fishsubs
from splash_model import DATA

try:
    from mss import MSS as Screen
except ImportError:
    from mss import mss as Screen

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

VK_F8 = 0x77
APP_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))


# ---------------------------------------------------------------- windows ---
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def list_windows():
    found = []

    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        cloaked = wintypes.DWORD()
        dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        length = user32.GetWindowTextLengthW(hwnd)
        if cloaked.value or not length:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append((hwnd, pid.value, title.value))
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return found


# ------------------------------------------------------------------ input ---
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def right_click():
    for flag in (0x0008, 0x0010):  # RIGHTDOWN, RIGHTUP
        event = INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, flag, 0, 0))
        user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))
        time.sleep(random.uniform(0.04, 0.09))


def not_ready(hwnd):
    """Why a click can't safely be sent now, or None. Clicks land wherever the mouse is."""
    if user32.GetForegroundWindow() != hwnd:
        return "Paused: click into the game window"
    point, rect = wintypes.POINT(), wintypes.RECT()
    user32.GetCursorPos(ctypes.byref(point))
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    if not (rect.left <= point.x < rect.right and rect.top <= point.y < rect.bottom):
        return "Paused: move the mouse over the game window"
    return None


def save_png(path, bgra):
    """Minimal PNG writer for the debug screenshot."""
    height, width = bgra.shape[:2]
    rgb = np.ascontiguousarray(bgra[:, :, [2, 1, 0]])
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(height))

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


# ----------------------------------------------------------------- fisher ---
class Fisher(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.model = fishsound.load_model(DATA)
        self.target = None  # (hwnd, pid)
        self.active = False
        # options, set by the GUI
        self.use_sound = True
        self.use_subs = True
        self.ignore_arrows = False
        self.threshold = 0.95
        self.timeout = 45.0
        self.settle = 2.0
        self.recast = 1.0
        self.reel_delay = 0.1   # seconds between a confirmed bite and reeling in
        self.reel_jitter = 0.05  # up to this much extra, random each time
        # state shown by the GUI
        self.status = "Pick the Minecraft window"
        self.bites = self.timeouts = self.rejected = 0
        self.sound_prob = 0.0
        self.sound_db = -120.0
        self.audio_error = None
        self.last_event = ""
        self.test_result = ""
        # internals
        self.hit = None  # (time, probability, pitch) of the latest sound match
        self.capture = None
        self.matcher = None
        self.reader = None
        self.screen = None
        self.good_factor = fishsubs.SCALE_ORDER[0]

    # -- audio --------------------------------------------------------------
    def set_target(self, hwnd, pid):
        self.active = False
        self.target = (hwnd, pid)
        self.hit = None
        self.stop_audio()
        self.audio_error = None
        try:
            from proctap import ProcessAudioCapture
            self.matcher = fishsound.SplashMatcher(self.model)
            self.capture = ProcessAudioCapture(pid, on_data=self._on_audio)
            self.capture.start()
        except Exception as error:
            self.capture = None
            self.audio_error = f"{type(error).__name__}: {error}"

    def stop_audio(self):
        if self.capture is not None:
            try:
                self.capture.stop()
            except Exception:
                pass
            self.capture = None

    def _on_audio(self, data, _frames):
        pcm = np.frombuffer(data, np.float32)
        pcm = pcm[:pcm.size // 2 * 2]
        if not pcm.size:
            return
        prob, pitch, db = self.matcher.feed(pcm.reshape(-1, 2).mean(axis=1))
        self.sound_prob, self.sound_db = prob, db
        if prob >= self.threshold:
            self.hit = (time.time(), prob, pitch)

    # -- subtitles ----------------------------------------------------------
    def read_subtitles(self, save_to=None):
        """(splash line or None, all text read). Tries the image sizes the OCR copes with."""
        if self.screen is None:
            self.screen = Screen()
        image = fishsubs.grab(*fishsubs.client_region(self.target[0]), self.screen)
        if save_to:
            save_png(save_to, image)
        order = [self.good_factor] + [f for f in fishsubs.SCALE_ORDER if f != self.good_factor]
        seen = []
        for factor in order:
            lines = self.reader.read(image, factor)
            seen += lines
            line = fishsubs.find_splash_line(lines)
            if line:
                self.good_factor = factor
                return line, seen
        return None, seen

    def test_subtitles(self):
        """Button handler: after a countdown, read the window corner once and report what the OCR sees."""
        try:
            if not self.target:
                self.test_result = "Pick the Minecraft window first"
                return
            for left in (3, 2, 1):
                self.test_result = f"Switch to Minecraft and trigger a sound... reading in {left}"
                time.sleep(1)
            if self.reader is None:
                self.reader = fishsubs.SubtitleReader()
            path = os.path.join(APPDIR_DEBUG, "autofish_debug.png")
            line, seen = self.read_subtitles(save_to=path)
            shown = " | ".join(seen) if seen else "(no text found)"
            verdict = "FOUND the splash line" if line else "no splash line"
            self.test_result = f"{verdict}. OCR read: {shown}   (screenshot: {path})"
        except Exception as error:
            self.test_result = f"Subtitle check failed: {type(error).__name__}: {error}"

    # -- fishing loop -------------------------------------------------------
    def sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end and self.active:
            time.sleep(0.01)
        return self.active

    def wait_for_focus(self):
        hwnd = self.target[0]
        while self.active:
            if not user32.IsWindow(hwnd):
                self.active = False
                self.status = "Window closed"
                return False
            problem = not_ready(hwnd)
            if problem is None:
                return True
            self.status = problem
            self.sleep(0.2)
        return False

    def prepare(self):
        if self.use_sound and (self.capture is None):
            self.status = f"Sound capture unavailable ({self.audio_error}). Untick 'Sound match' to use subtitles only."
            self.active = False
            return False
        if self.use_subs and self.reader is None:
            try:
                self.reader = fishsubs.SubtitleReader()
            except Exception as error:
                self.status = f"Subtitle OCR unavailable ({error}). Untick 'Subtitle text' to use sound only."
                self.active = False
                return False
        return True

    def confirm(self, hit):
        """A sound match happened; check the subtitle (if enabled) before accepting."""
        if not self.use_subs:
            self.last_event = f"bite: sound match {hit[1]:.2f} (pitch {hit[2]:.1f})"
            return True
        for _ in range(4):
            line, seen = self.read_subtitles()
            if line:
                if self.ignore_arrows and re.search(r"[<>]", line):
                    self.rejected += 1
                    self.last_event = f"ignored: splash came from the side ({line!r})"
                    return False
                self.last_event = f"bite: sound match {hit[1]:.2f} + subtitle {line!r}"
                return True
            time.sleep(0.1)
        self.rejected += 1
        self.last_event = f"ignored: sound match {hit[1]:.2f} but no splash subtitle (read: {' | '.join(seen) or 'nothing'})"
        return False

    def listen(self):
        """True on a confirmed bite; False on timeout or stop."""
        self.status = "Waiting for a bite"
        start = last_poll = consumed = time.time()
        # Subtitle-only mode: a splash line left over from the last bite lasts ~3 s, so it only counts
        # as new once the corner has been seen without one.
        seen_clear = False
        while self.active:
            now = time.time()
            if now - start > self.timeout:
                return False
            problem = not_ready(self.target[0])
            if problem:
                self.status = problem
                start += 0.05
                time.sleep(0.05)
                continue
            self.status = "Waiting for a bite"
            if self.use_sound:
                hit = self.hit
                if hit and hit[0] > consumed:
                    consumed = hit[0] + 1.0
                    if self.confirm(hit):
                        return True
            elif now - last_poll > 0.25:
                last_poll = now
                line, _ = self.read_subtitles()
                if line is None:
                    seen_clear = True
                elif seen_clear:
                    self.last_event = f"bite: subtitle {line!r}"
                    return True
            time.sleep(0.01)
        return False

    def fish_once(self):
        if not self.prepare() or not self.wait_for_focus():
            return
        self.status = "Casting"
        right_click()
        if not self.sleep(self.settle):
            return
        bitten = self.listen()
        if not self.active:
            return
        if bitten:
            self.bites += 1
            self.status = "Bite! Reeling in"
            if not self.sleep(self.reel_delay + random.uniform(0, self.reel_jitter)):
                return
        else:
            self.timeouts += 1
            self.status = "No bite, recasting"
        if not self.wait_for_focus():
            return
        right_click()
        self.sleep(self.recast)

    def run(self):
        while True:
            if self.active and self.target:
                try:
                    self.fish_once()
                except Exception as error:
                    self.active = False
                    self.status = f"Stopped by an error: {type(error).__name__}: {error}"
            else:
                time.sleep(0.05)


APPDIR_DEBUG = APP_DIR


# -------------------------------------------------------------------- GUI ---
class App:
    BAR_W, BAR_H = 360, 20

    def __init__(self, root):
        self.root = root
        root.title("Minecraft Auto-Fisher")
        root.resizable(False, False)
        self.fisher = Fisher()
        self.windows = []
        self.shown_prob = 0.0
        self.f8_was_down = False

        frame = ttk.Frame(root, padding=12)
        frame.grid()

        ttk.Label(frame, text="Game window").grid(row=0, column=0, sticky="w")
        self.combo = ttk.Combobox(frame, state="readonly", width=52)
        self.combo.grid(row=1, column=0, sticky="we")
        self.combo.bind("<<ComboboxSelected>>", self.pick_window)
        ttk.Button(frame, text="Refresh", command=self.refresh).grid(row=1, column=1, padx=(6, 0))

        box = ttk.LabelFrame(frame, text="A bite needs every ticked check to agree", padding=8)
        box.grid(row=2, column=0, columnspan=2, sticky="we", pady=(12, 0))
        self.use_sound = tk.BooleanVar(value=True)
        self.use_subs = tk.BooleanVar(value=True)
        self.no_sides = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="Sound match: the bobber-splash sound in the game's audio",
                        variable=self.use_sound).grid(row=0, column=0, sticky="w")
        self.canvas = tk.Canvas(box, width=self.BAR_W, height=self.BAR_H, bg="#222", highlightthickness=0)
        self.canvas.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.sound_label = ttk.Label(box, text="")
        self.sound_label.grid(row=2, column=0, sticky="w")
        row = ttk.Frame(box)
        row.grid(row=3, column=0, sticky="w")
        ttk.Label(row, text="Strictness").grid(row=0, column=0)
        self.strict = tk.IntVar(value=38)
        ttk.Scale(row, from_=0, to=100, variable=self.strict, length=250).grid(row=0, column=1, padx=6)
        self.strict_label = ttk.Label(row, text="")
        self.strict_label.grid(row=0, column=2)

        ttk.Checkbutton(box, text="Subtitle text: 'Fishing Bobber splashes' shown on screen",
                        variable=self.use_subs).grid(row=4, column=0, sticky="w", pady=(10, 0))
        ttk.Checkbutton(box, text="   Ignore splashes from the side (subtitle shows < > arrows)",
                        variable=self.no_sides).grid(row=5, column=0, sticky="w")
        test = ttk.Frame(box)
        test.grid(row=6, column=0, sticky="we", pady=(4, 0))
        ttk.Button(test, text="Test subtitles", command=self.test_subtitles).grid(row=0, column=0, sticky="w")
        self.test_label = ttk.Label(test, text="", wraplength=290, foreground="#555")
        self.test_label.grid(row=0, column=1, sticky="w", padx=8)

        opts = ttk.Frame(frame)
        opts.grid(row=3, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self.timeout = self.spin(opts, 0, "Give up after (s)", 45, 10, 300)
        self.settle = self.spin(opts, 1, "Ignore after cast (s)", 2.0, 0.5, 10)
        self.recast = self.spin(opts, 2, "Wait before recast (s)", 1.0, 0.3, 10)
        self.reel_delay = self.spin(opts, 3, "Reel in this long after a bite (s)", 0.1, 0.0, 1.5, step=0.05)
        self.reel_jitter = self.spin(opts, 4, "  plus a random extra up to (s)", 0.05, 0.0, 1.0, step=0.05)
        ttk.Label(opts, foreground="#666", wraplength=420,
                  text="The fish slips away about 1-2 s after the splash and detection takes ~0.5 s, "
                       "so keep the total under ~0.4 s.").grid(row=5, column=0, columnspan=2, sticky="w")

        self.button = ttk.Button(frame, text="Start (F8)", command=self.toggle)
        self.button.grid(row=4, column=0, columnspan=2, sticky="we", pady=(14, 4))
        self.status = ttk.Label(frame, text="", wraplength=430)
        self.status.grid(row=5, column=0, columnspan=2, sticky="w")
        self.event = ttk.Label(frame, text="", wraplength=430, foreground="#555")
        self.event.grid(row=6, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, foreground="#666", wraplength=430,
                  text="Minecraft Java, English. Turn on Options > Accessibility Settings > Show Subtitles "
                       "(called Closed Captions in 26.x). "
                       "Hold the rod, aim at water, then press Start or F8. Keep the game window in front.").grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self.refresh()
        self.fisher.start()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll()

    @staticmethod
    def spin(parent, row, label, value, low, high, step=0.5):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w")
        var = tk.StringVar(value=str(value))
        ttk.Spinbox(parent, from_=low, to=high, increment=step, format="%.2f", textvariable=var, width=7).grid(
            row=row, column=1, padx=(8, 0), pady=1)
        return var

    def close(self):
        self.fisher.active = False
        self.fisher.stop_audio()
        self.root.destroy()

    def refresh(self):
        self.windows = [w for w in list_windows() if w[2] != self.root.title()]
        self.windows.sort(key=lambda w: "minecraft" not in w[2].lower())
        self.combo["values"] = [f"{title}  (pid {pid})" for _, pid, title in self.windows]
        if self.windows and "minecraft" in self.windows[0][2].lower():
            self.combo.current(0)
            self.pick_window()

    def pick_window(self, _event=None):
        hwnd, pid, _ = self.windows[self.combo.current()]
        self.fisher.set_target(hwnd, pid)
        self.fisher.status = "Ready"

    def test_subtitles(self):
        threading.Thread(target=self.fisher.test_subtitles, daemon=True).start()

    def toggle(self):
        f = self.fisher
        if not f.target:
            f.status = "Pick the Minecraft window first"
        elif not (self.use_sound.get() or self.use_subs.get()):
            f.status = "Tick at least one check"
        elif f.active:
            f.active = False
            f.status = "Stopped"
        else:
            f.status = "Starting"
            f.active = True

    @staticmethod
    def number(var, default):
        try:
            return float(var.get())
        except ValueError:
            return default

    def poll(self):
        f = self.fisher
        f.use_sound, f.use_subs = self.use_sound.get(), self.use_subs.get()
        f.ignore_arrows = self.no_sides.get()
        f.threshold = 1 - 0.2 * 0.025 ** (self.strict.get() / 100)
        f.timeout = self.number(self.timeout, 45)
        f.settle = self.number(self.settle, 2)
        f.recast = self.number(self.recast, 1)
        f.reel_delay = max(0.0, self.number(self.reel_delay, 0.1))
        f.reel_jitter = max(0.0, self.number(self.reel_jitter, 0.05))

        f8_down = bool(user32.GetAsyncKeyState(VK_F8) & 0x8000)
        if f8_down and not self.f8_was_down:
            self.toggle()
        self.f8_was_down = f8_down

        self.shown_prob = max(f.sound_prob, self.shown_prob * 0.97)
        self.draw_bar(f.threshold)
        self.strict_label.config(text=f"match >= {f.threshold:.3f}")
        if f.audio_error:
            self.sound_label.config(text=f"Sound capture unavailable: {f.audio_error}")
        else:
            self.sound_label.config(text=f"Splash match {self.shown_prob:.2f}   (game audio level {f.sound_db:.0f} dB)")
        self.button.config(text="Stop (F8)" if f.active else "Start (F8)")
        self.status.config(text=f"{f.status}   |   bites: {f.bites}   timeouts: {f.timeouts}   ignored: {f.rejected}")
        self.event.config(text=f.last_event)
        self.test_label.config(text=f.test_result)
        self.root.after(30, self.poll)

    def draw_bar(self, threshold):
        c = self.canvas
        c.delete("all")
        fill = min(1.0, self.shown_prob) * self.BAR_W
        color = "#3a9" if self.shown_prob >= threshold else "#567"
        c.create_rectangle(0, 0, fill, self.BAR_H, fill=color, width=0)
        x = threshold * self.BAR_W
        c.create_line(x, 0, x, self.BAR_H, fill="#e33", width=2)


def selftest():
    """Checks every component loads on this PC; writes autofish_selftest.txt next to the program."""
    results = []

    def check(name, fn):
        try:
            detail = fn()
            results.append(f"OK    {name}" + (f": {detail}" if detail is not None else ""))
        except Exception as error:
            results.append(f"FAIL  {name}: {type(error).__name__}: {error}")

    def sound_model():
        matcher = fishsound.SplashMatcher(fishsound.load_model(DATA))
        matcher.feed(np.zeros(48000, np.float32))

    def ocr():
        reader = fishsubs.SubtitleReader()
        reader.read(np.zeros((200, 400, 4), np.uint8))

    def process_audio():
        from proctap import ProcessAudioCapture
        capture = ProcessAudioCapture(os.getpid(), on_data=lambda data, frames: None)
        capture.start()
        capture.stop()

    def screen():
        with Screen() as sct:
            fishsubs.grab(0, 0, 32, 32, sct)

    check("splash sound model", sound_model)
    check("Windows OCR (subtitle reading)", ocr)
    check("per-app audio capture", process_audio)
    check("screen capture", screen)
    check("window list", lambda: f"{len(list_windows())} windows")
    with open(os.path.join(APP_DIR, "autofish_selftest.txt"), "w") as f:
        f.write("\n".join(results) + "\n")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        root = tk.Tk()
        App(root)
        root.mainloop()
