# PyInstaller spec for macOS TuneSync.app
# Build:
#   .venv/bin/python -m PyInstaller TuneSync.spec

from pathlib import Path
import os

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# PyInstaller executes the spec via exec() without reliably defining __file__.
# The build script runs from repo root, so use CWD.
repo_root = Path(os.getcwd()).resolve()

icon_file = repo_root / "assets" / "TuneSync.icns"

# Include UI assets (icons + onboarding images)
datas = [
    (str(repo_root / "tunesync_app" / "ui" / "icons"), "tunesync_app/ui/icons"),
    (str(repo_root / "tunesync_app" / "ui" / "images"), "tunesync_app/ui/images"),
]

# Bundle the imageio-ffmpeg packaged binaries (ffmpeg) for self-contained builds.
try:
    datas += collect_data_files("imageio_ffmpeg")
except Exception:
    pass

# Hidden imports:
# - `main` is used by the GUI worker mode via runpy.run_module('main')
hiddenimports = [
    "main",
    "imageio_ffmpeg",
]


a = Analysis(
    [str(repo_root / "tunesync_app" / "__main__.py")],
    pathex=[str(repo_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TuneSync",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="TuneSync",
)

app = BUNDLE(
    coll,
    name="TuneSync.app",
    icon=str(icon_file) if icon_file.exists() else None,
    bundle_identifier="com.tunesync.app",
)
