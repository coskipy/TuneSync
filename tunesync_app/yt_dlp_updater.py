"""yt-dlp auto-updater for TuneSync packaged app.

YouTube regularly changes its API, which breaks yt-dlp. Since yt-dlp is frozen
inside the PyInstaller bundle and cannot be pip-installed at runtime, this module
implements a lightweight self-updater:

  1. apply_update()          - fast, no network; prepends any cached update to
                               sys.path so the fresh yt_dlp package takes priority
                               over the frozen one. Call before any yt_dlp import.

  2. start_background_update() - spawns a daemon thread that silently checks PyPI,
                               downloads the latest pure-Python wheel, and extracts
                               it to Application Support. Takes effect next launch.

Storage layout:
  ~/Library/Application Support/TuneSync/yt_dlp_update/
      version.txt          # version of the extracted package, e.g. "2026.3.3"
      yt_dlp/              # extracted package (prepended to sys.path)
          __init__.py
          ...
"""
from __future__ import annotations

import json
import shutil
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path

_UPDATE_DIR = (
    Path.home() / "Library" / "Application Support" / "TuneSync" / "yt_dlp_update"
)


def _cached_version() -> str | None:
    try:
        return (_UPDATE_DIR / "version.txt").read_text().strip()
    except OSError:
        return None


def apply_update() -> None:
    """Prepend the cached update directory to sys.path.

    Must be called before any yt_dlp import. No network I/O — returns instantly.
    Does nothing if no update has been downloaded yet.
    """
    if (
        _cached_version() is not None
        and (_UPDATE_DIR / "yt_dlp" / "__init__.py").exists()
    ):
        site = str(_UPDATE_DIR)
        if site not in sys.path:
            sys.path.insert(0, site)


def start_background_update() -> None:
    """Spawn a daemon thread that silently downloads the latest yt-dlp from PyPI.

    The update is extracted to Application Support and takes effect on the next
    app launch. Never blocks the UI or raises exceptions to the caller.
    """
    t = threading.Thread(
        target=_check_and_update, daemon=True, name="yt-dlp-updater"
    )
    t.start()


def _normalise(version: str) -> tuple[int, ...]:
    """Normalise version strings for comparison.

    yt_dlp.__version__ uses zero-padded components ('2026.03.03') while PyPI
    returns them without padding ('2026.3.3'). Convert both to int tuples so
    they compare equal.
    """
    try:
        return tuple(int(p) for p in version.split("."))
    except ValueError:
        return (0,)


def _check_and_update() -> None:
    try:
        # What version is the app currently running?
        import yt_dlp.version as _ytv  # noqa: PLC0415

        running: str = getattr(_ytv, "__version__", "")
        if not running:
            return

        # Fetch latest metadata from PyPI.
        req = urllib.request.Request(
            "https://pypi.org/pypi/yt-dlp/json",
            headers={"User-Agent": "TuneSync-yt-dlp-updater/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data: dict = json.loads(resp.read())

        latest: str = data["info"]["version"]

        # Nothing to do if we're already on the latest version.
        # Also skip if we already have this exact version cached (avoids
        # re-downloading across restarts before the user syncs).
        if _normalise(latest) == _normalise(running):
            return
        cached = _cached_version()
        if cached and _normalise(latest) == _normalise(cached):
            return

        # Find the pure-Python wheel (no platform-specific binary needed).
        wheel_url: str | None = None
        for file_info in data.get("urls", []):
            if file_info["filename"].endswith("-py3-none-any.whl"):
                wheel_url = file_info["url"]
                break
        if not wheel_url:
            return

        _UPDATE_DIR.mkdir(parents=True, exist_ok=True)

        # Download wheel to a temp path.
        tmp_wheel = _UPDATE_DIR / f"_download-{latest}.whl"
        with urllib.request.urlopen(wheel_url, timeout=120) as resp:
            tmp_wheel.write_bytes(resp.read())

        # Extract yt_dlp/ into a staging directory.
        staging = _UPDATE_DIR / "_staging"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()

        with zipfile.ZipFile(tmp_wheel) as zf:
            for name in zf.namelist():
                if name.startswith("yt_dlp/"):
                    zf.extract(name, staging)

        # Sanity-check the extracted package.
        if not (staging / "yt_dlp" / "__init__.py").exists():
            raise RuntimeError("Extracted package missing __init__.py")

        # Atomic-ish swap: rotate live → _old, staging/yt_dlp → live.
        live = _UPDATE_DIR / "yt_dlp"
        old = _UPDATE_DIR / "_yt_dlp_old"
        if live.exists():
            if old.exists():
                shutil.rmtree(old, ignore_errors=True)
            live.rename(old)
        (staging / "yt_dlp").rename(live)
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)

        # Commit: write the version marker only after a successful extraction.
        (_UPDATE_DIR / "version.txt").write_text(latest)

        # Clean up temp files.
        tmp_wheel.unlink(missing_ok=True)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    except Exception:
        # Never crash the app due to an update failure.
        pass
