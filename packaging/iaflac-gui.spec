# PyInstaller spec for the flacmn desktop app (macOS .app and Windows folder build). The app is named flacmn; the
# Python package and the command line tool stay iaflac.
#   pyinstaller packaging/iaflac-gui.spec --noconfirm
# Expects the external tools in packaging/bin/ (flac, metaflac, ffmpeg; .exe on Windows): see packaging/README.md.
# They end up in <bundle>/bin, where iaflac.binaries looks first.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
BIN = ROOT / "packaging" / "bin"
VERSION = "0.1.7"

tools = [(str(p), "bin") for p in sorted(BIN.glob("*")) if p.is_file() and p.name != ".gitkeep"] if BIN.is_dir() else []
if not tools:
    print("WARNING: packaging/bin is empty; the app will look for flac/ffmpeg on PATH", file=sys.stderr)

datas = [
    (str(ROOT / "iaflac" / "gui" / "static"), "iaflac/gui/static"),
    (str(ROOT / "genres.yml"), "."),
]
hidden = (collect_submodules("uvicorn") + collect_submodules("iaflac") +
          ["webview.platforms.edgechromium" if sys.platform == "win32" else "webview.platforms.cocoa"])

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT)],
    binaries=tools,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "pytest", "IPython", "matplotlib.tests", "numpy.tests", "scipy.tests", "PyQt5", "PyQt6", "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure)
icon_win = ROOT / "packaging" / "icons" / "iaflac.ico"
icon_mac = ROOT / "packaging" / "icons" / "iaflac.icns"

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="flacmn",
    console=False,
    icon=str(icon_win) if sys.platform == "win32" and icon_win.exists() else None,
    target_arch=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="flacmn")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="flacmn.app",
        icon=str(icon_mac) if icon_mac.exists() else None,
        bundle_identifier="com.spcmn.flacmn",
        version=VERSION,
        info_plist={
            "CFBundleName": "flacmn",
            "CFBundleDisplayName": "flacmn",
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        },
    )
