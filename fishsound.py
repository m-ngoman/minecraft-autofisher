"""Recognises the bobber-splash sound in a stream of game audio.

The bobber splash is Minecraft's random/splash.ogg played at a random pitch
(0.6x to 1.4x). One small logistic classifier per pitch looks at the log-band
spectrogram of the last ~0.3-0.65 s of audio, so loudness and gain don't
matter. The weights were trained by tools/train_classifier.py on the real game
sounds and are embedded in splash_model.py.
"""
import base64
import io

import numpy as np

SR = 48000
N_FFT = 1024
HOP = 480  # 10 ms
N_BANDS = 20
F_LO, F_HI = 300.0, 15000.0
PITCHES = (0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4)
MIN_DB = -10.0  # ignore windows whose loudest frame is quieter than this
_WINDOW = np.hanning(N_FFT).astype(np.float32)


def _band_matrix():
    edges = np.geomspace(F_LO, F_HI, N_BANDS + 1)
    freqs = np.fft.rfftfreq(N_FFT, 1 / SR)
    matrix = np.zeros((N_BANDS, freqs.size), np.float32)
    for i in range(N_BANDS):
        members = (freqs >= edges[i]) & (freqs < edges[i + 1])
        if not members.any():
            members[np.argmin(np.abs(freqs - (edges[i] * edges[i + 1]) ** 0.5))] = True
        matrix[i, members] = 1.0 / members.sum()
    return matrix


_BANDS = _band_matrix()


def log_bands(mono):
    """(frames, N_BANDS) log10 band power of a mono float32 signal."""
    count = (len(mono) - N_FFT) // HOP + 1
    if count <= 0:
        return np.zeros((0, N_BANDS), np.float32)
    index = np.arange(N_FFT)[None, :] + HOP * np.arange(count)[:, None]
    power = np.abs(np.fft.rfft(mono[index] * _WINDOW, axis=1)) ** 2
    return np.log10(power @ _BANDS.T + 1e-9).astype(np.float32)


def frame_db(feats):
    """Overall level of each frame in dB (sum of band power)."""
    return 10 * np.log10((10.0 ** feats).sum(axis=1) + 1e-12)


def repitch(mono, factor):
    """Play mono back `factor` times faster, like OpenAL pitch."""
    positions = np.arange(0, len(mono) - 1, factor)
    return np.interp(positions, np.arange(len(mono)), mono).astype(np.float32)


def load_model(text):
    """[(pitch, weights, bias, window_frames)] from splash_model.DATA."""
    data = np.load(io.BytesIO(base64.b64decode(text)))
    return [(float(p), data[f"w{i}"].astype(np.float32), float(data["b"][i]), int(data["n"][i]))
            for i, p in enumerate(data["pitches"])]


class SplashMatcher:
    """Feed it mono 48 kHz audio; it reports how splash-like the newest audio is."""

    def __init__(self, model):
        self.model = model
        self.keep = max(n for _, _, _, n in model)
        self.tail = np.zeros(0, np.float32)
        self.feats = np.zeros((0, N_BANDS), np.float32)
        self.db = np.zeros(0, np.float32)

    def feed(self, mono):
        """Returns (best probability 0..1, its pitch, loudest frame level in dB) for the new audio."""
        samples = np.concatenate([self.tail, np.asarray(mono, np.float32)])
        count = (len(samples) - N_FFT) // HOP + 1
        if count <= 0:
            self.tail = samples
            return 0.0, 0.0, float(self.db[-1]) if len(self.db) else -120.0
        new = log_bands(samples[:HOP * (count - 1) + N_FFT])
        self.tail = samples[HOP * count:]
        self.feats = np.concatenate([self.feats, new])[-(self.keep + len(new)):]
        self.db = frame_db(self.feats)
        best, best_pitch = 0.0, 0.0
        for end in range(len(self.feats) - len(new), len(self.feats)):
            for pitch, weights, bias, n in self.model:
                if end + 1 < n or self.db[end + 1 - n:end + 1].max() < MIN_DB:
                    continue
                window = self.feats[end + 1 - n:end + 1]
                window = window - window.mean()
                spread = window.std()
                if spread < 0.05:
                    continue
                z = float((window / spread).ravel() @ weights) + bias
                prob = 1.0 / (1.0 + np.exp(-z))
                if prob > best:
                    best, best_pitch = prob, pitch
        return best, best_pitch, float(self.db[-len(new):].max())
