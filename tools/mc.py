"""Dev helper: drive the Minecraft window with SendInput and screenshot it.

Usage: mc.py "click 960 443; wait 1.5; key esc; type hello; shot out.png"
Commands: click X Y | rclick X Y | move X Y | rel DX DY | key NAME | hold NAME SECONDS | type TEXT | wait S | shot PATH
Coordinates are screen pixels (the game is fullscreen at 0,0).
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import autofish
import fishsubs
import mss

user32 = ctypes.windll.user32
SCREEN_W, SCREEN_H = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
VK = {"esc": 0x1B, "enter": 0x0D, "space": 0x20, "tab": 0x09, "shift": 0x10, "ctrl": 0x11, "backspace": 0x08,
      "end": 0x23, "home": 0x24, "delete": 0x2E, "slash": 0xBF, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "f1": 0x70, "f3": 0x72, "f11": 0x7A}
VK.update({c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"})


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", autofish.MOUSEINPUT), ("ki", KEYBDINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def send(inp):
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def mouse(flags, x=0, y=0):
    send(INPUT(type=0, mi=autofish.MOUSEINPUT(x, y, 0, flags, 0, 0)))


def move_to(x, y):
    mouse(0x0001 | 0x8000, int(x * 65535 / (SCREEN_W - 1)), int(y * 65535 / (SCREEN_H - 1)))
    time.sleep(0.15)


def click(x, y, right=False):
    move_to(x, y)
    mouse(0x0008 if right else 0x0002)
    time.sleep(0.06)
    mouse(0x0010 if right else 0x0004)
    time.sleep(0.25)


def key_event(vk, up=False):
    scan = user32.MapVirtualKeyW(vk, 0)
    send(INPUT(type=1, ki=KEYBDINPUT(vk, scan, 0x0002 if up else 0, 0, 0)))


def key(name, seconds=0.06):
    vk = VK[name.lower()]
    key_event(vk)
    time.sleep(seconds)
    key_event(vk, up=True)
    time.sleep(0.15)


def type_text(text):
    for ch in text:
        for up in (False, True):
            send(INPUT(type=1, ki=KEYBDINPUT(0, ord(ch), 0x0004 | (0x0002 if up else 0), 0, 0)))
        time.sleep(0.04)
    time.sleep(0.2)


def shot(path):
    hwnd = next(w[0] for w in autofish.list_windows() if w[2].startswith("Minecraft"))
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    with mss.MSS() as sct:
        img = fishsubs.grab(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top, sct)
    autofish.save_png(path, img)


for step in sys.argv[1].split(";"):
    cmd, *args = step.strip().split(" ", 1)[0:1] + step.strip().split(" ")[1:] if False else step.strip().split(" ")
    if not cmd:
        continue
    if cmd == "click": click(float(args[0]), float(args[1]))
    elif cmd == "rclick": click(float(args[0]), float(args[1]), right=True)
    elif cmd == "move": move_to(float(args[0]), float(args[1]))
    elif cmd == "rel": mouse(0x0001, int(args[0]), int(args[1])); time.sleep(0.1)
    elif cmd == "key": key(args[0])
    elif cmd == "hold": key(args[0], float(args[1]))
    elif cmd == "type": type_text(" ".join(args))
    elif cmd == "wait": time.sleep(float(args[0]))
    elif cmd == "shot": shot(args[0])
    else: sys.exit(f"unknown command {cmd}")
