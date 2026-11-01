# Minecraft Auto-Fisher

An external auto-fishing macro for **Minecraft Java Edition on Windows**. Nothing is injected into the game: it
listens to the game's audio, reads the on-screen captions, and sends ordinary right-clicks.

A bite is only accepted when **both** checks agree (each can be switched off in the window):

- **Sound match** - the bobber-splash sound is in the game's own audio. A small classifier trained on the real game
  sounds recognises it at any pitch, and only that game's audio is captured (not Discord or music).
- **Caption text** - the on-screen caption reads "Fishing Bobber splashes" (screenshot of the window corner read with
  Windows' built-in OCR).

Footsteps, chests, rain, other captions, or a caption with no matching sound do not trigger it.

## Download and use

1. Download `MinecraftAutoFisher.exe` from the [latest release](../../releases/latest). Windows SmartScreen may warn
   because the file is unsigned: click **More info > Run anyway**.
2. In Minecraft turn on captions: **Options > Accessibility Settings > Show Subtitles** (called **Closed Captions** in 26.x).
3. Hold the fishing rod, aim at water, keep the game window in front.
4. Pick the Minecraft window in the app, then press **Start** or **F8**. The first click casts.

Settings in the window:

| Setting | What it does |
| --- | --- |
| Strictness | How closely the sound must match. Lower it if real bites are ignored. |
| Ignore splashes from the side | Skips splashes whose caption shows `<` `>` arrows (off by default, little tested). |
| Give up after | Recasts if nothing bites for this long. |
| Ignore after cast | Seconds to ignore the cast's own sounds. |
| Wait before recast | Pause after reeling in. |
| Reel in this long after a bite (+ random extra) | Delay before reeling in. The fish escapes ~1-2 s after the splash and detection takes ~0.5 s, so keep the total under ~0.4 s. |

The **Test subtitles** button shows what the OCR reads from the game (and saves `autofish_debug.png` next to the program).
Run `MinecraftAutoFisher.exe --selftest` to write `autofish_selftest.txt`, which lists whether each part loads.

It only clicks while the game window is in front **and** the mouse is over it. Many multiplayer servers forbid
auto-fishing macros, so check the rules before using it online.

Requirements: Windows 10 (2004 or newer) or 11, Minecraft Java in English.

## Run or build from source

```powershell
pip install -r requirements.txt
python autofish.py                 # run the app
pip install pyinstaller
.\build.ps1                        # builds dist\MinecraftAutoFisher.exe
```

## How it works

- `autofish.py` - window picker GUI, fishing loop, mouse and focus guards.
- `fishsound.py` + `splash_model.py` - splash-sound recognition (log-band spectrogram + one logistic classifier per pitch).
- `fishsubs.py` - screen grab of the caption corner and Windows OCR.
- `tools/` - development helpers: `train_classifier.py` regenerates `splash_model.py` from a local Minecraft asset
  index, and the rest are test harnesses (a stand-in game window, an end-to-end test, caption watchers).

The classifier was trained on sounds from the game's own assets on the developer's machine; those assets are not
included in this repository.
