# PyInstaller spec for ClipForge: builds a no-terminal Windows app.
#   pyinstaller clipforge.spec
# Produces dist/ClipForge/ClipForge.exe (onedir; reliable for heavy libs).
import os
from PyInstaller.utils.hooks import collect_all

block_cipher = None

datas = [("static", "static"), ("extension", "extension")]
binaries = []

# bundle ffmpeg/ffprobe if a bin/ folder is present (CI drops static builds here)
if os.path.isdir("bin"):
    for f in os.listdir("bin"):
        binaries.append((os.path.join("bin", f), "bin"))

hiddenimports = []
# libraries with dynamic imports / data files that PyInstaller misses alone
for pkg in [
    "librosa", "soundfile", "soxr", "audioread", "pooch", "lazy_loader",
    "numba", "llvmlite", "scipy", "sklearn", "joblib", "decorator",
    "webview", "clr_loader", "proxy_tools", "bottle", "imageio_ffmpeg",
]:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

a = Analysis(
    ["desktop.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PySide2"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ClipForge",
    debug=False,
    strip=False,
    upx=False,
    console=False,           # no terminal window
    icon="extension/icon.ico" if os.path.exists("extension/icon.ico") else None,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, name="ClipForge",
)
