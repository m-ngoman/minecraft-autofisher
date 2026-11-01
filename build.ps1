# Builds dist\MinecraftAutoFisher.exe (one file, no Python needed on the target PC).
# Needs a Python with: proc-tap mss numpy pyinstaller winrt-runtime winrt-Windows.Media.Ocr
#   winrt-Windows.Graphics.Imaging winrt-Windows.Storage.Streams winrt-Windows.Foundation
#   winrt-Windows.Foundation.Collections winrt-Windows.Globalization
param([string]$Python = "python")
Set-Location $PSScriptRoot
& $Python -m PyInstaller --onefile --noconsole --name MinecraftAutoFisher `
    --distpath dist --workpath "$env:TEMP\mcfish-build" --specpath "$env:TEMP\mcfish-build" `
    --paths $PSScriptRoot `
    --collect-all proctap --collect-submodules winrt --collect-binaries winrt `
    --hidden-import winrt.windows.media.ocr --hidden-import winrt.windows.graphics.imaging `
    --hidden-import winrt.windows.storage.streams --hidden-import winrt.windows.foundation `
    --hidden-import winrt.windows.foundation.collections --hidden-import winrt.windows.globalization `
    --exclude-module scipy --exclude-module sklearn --exclude-module PIL --exclude-module soundfile `
    --exclude-module pandas --exclude-module matplotlib `
    autofish.py
