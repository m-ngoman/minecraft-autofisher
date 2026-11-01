"""Reads Minecraft's on-screen subtitles (Options > Accessibility > Show Subtitles).

Grabs the bottom-right corner of the game window and runs Windows' built-in
OCR on it, looking for the "Fishing Bobber splashes" line.
"""
import asyncio
import ctypes
import difflib
import re
from ctypes import wintypes

import numpy as np

user32 = ctypes.windll.user32
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]

# Subtitles stack up from the bottom-right corner of the window.
REGION_RIGHT = 0.5   # fraction of the window width, measured from the right edge
REGION_BOTTOM = 0.6  # fraction of the window height, measured from the bottom edge
SCALE_ORDER = (1, -2, 2)  # image sizes to try, see SubtitleReader.read


def client_region(hwnd):
    """Screen rectangle (left, top, width, height) of the subtitle corner of the window."""
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    width, height = rect.right, rect.bottom
    w, h = int(width * REGION_RIGHT), int(height * REGION_BOTTOM)
    return origin.x + width - w, origin.y + height - h, w, h


def _close(word, target, cutoff=0.75):
    return difflib.SequenceMatcher(None, word, target).ratio() >= cutoff


def find_splash_line(lines):
    """The OCR line that reads 'Fishing Bobber splashes', or None.

    Both words are required, so 'Bobber thrown', 'Bobber retrieved' and the
    generic 'Splashing' subtitle never match.
    """
    for line in lines:
        words = re.sub(r"[^a-z ]", " ", line.lower()).split()
        if any(_close(w, "bobber") for w in words) and any(_close(w, "splashes") for w in words):
            return line
    return None


class SubtitleReader:
    def __init__(self):
        from winrt.windows.media.ocr import OcrEngine
        self.engine = OcrEngine.try_create_from_user_profile_languages()
        if self.engine is None:
            raise RuntimeError("Windows has no OCR language installed (Settings > Time & language > Language)")
        self.max_dim = OcrEngine.max_image_dimension

    def read(self, bgra, factor=1):
        """OCR a BGRA image (uint8 array, height x width x 4); returns the text lines.

        factor 2 enlarges the image and -2 shrinks it, since the OCR is picky
        about text size and the in-game text size depends on the GUI scale.
        """
        image = np.asarray(bgra, np.uint8)
        if factor > 1:
            image = np.repeat(np.repeat(image, factor, axis=0), factor, axis=1)
        elif factor < -1:
            image = image[::-factor, ::-factor]
        if max(image.shape[:2]) > self.max_dim:
            return []
        return asyncio.run(self._recognize(np.ascontiguousarray(image)))

    def find_splash(self, bgra):
        """(matching line or None, all text read) trying the normal, shrunk and enlarged image."""
        seen = []
        for factor in SCALE_ORDER:
            lines = self.read(bgra, factor)
            seen += lines
            line = find_splash_line(lines)
            if line:
                return line, seen
        return None, seen

    async def _recognize(self, image):
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import DataWriter
        writer = DataWriter()
        writer.write_bytes(image.tobytes())
        bitmap = SoftwareBitmap.create_copy_from_buffer(
            writer.detach_buffer(), BitmapPixelFormat.BGRA8, image.shape[1], image.shape[0])
        result = await self.engine.recognize_async(bitmap)
        return [line.text for line in result.lines]


def grab(left, top, width, height, sct):
    """Screenshot of a screen rectangle as a BGRA numpy array."""
    shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
    return np.frombuffer(shot.bgra, np.uint8).reshape(shot.height, shot.width, 4)
