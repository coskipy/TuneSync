"""yt-dlp auto-updater for TuneSync packaged app.

YouTube regularly changes its API, which breaks yt-dlp. Since yt-dlp is frozen
inside the PyInstaller bundle and cannot be pip-installed at runtime, this module
implements a lightweight self-updater:

  1. apply_update()          - fast, no network; prepends any cached update to
                               sys.path so the fresh yt_dlp package takes priority
                               over the bundled one (only if it's actually newer).
                               Call before any yt_dlp import.

  2. start_background_update() - called on every GUI launch; spawns a daemon thread
                               that checks PyPI, downloads the latest pure-Python
                               wheel if it's newer, and extracts it to Application
                               Support. Sync workers are separate processes that call
                               apply_update() on startup, so the update takes effect
                               for the next sync in the same session. The GUI holds
                               a sync briefly while is_update_running() is True.

Storage layout:
  ~/Library/Application Support/TuneSync/yt_dlp_update/
      version.txt          # version of the extracted package, e.g. "2026.3.3"
      update.log           # outcome of the most recent update check
      yt_dlp/              # extracted package (prepended to sys.path)
          __init__.py
          ...
"""
from __future__ import annotations

import json
import shutil
import ssl
import sys
import threading
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

_UPDATE_DIR = (
    Path.home() / "Library" / "Application Support" / "TuneSync" / "yt_dlp_update"
)

_thread: threading.Thread | None = None


def _cached_version() -> str | None:
    try:
        return (_UPDATE_DIR / "version.txt").read_text().strip()
    except OSError:
        return None


def _bundled_version() -> str | None:
    """Version of the yt-dlp shipped with the app (or installed in the venv).

    Reads package metadata rather than importing yt_dlp, so it doesn't pin the
    bundled copy in sys.modules. Returns None if metadata isn't available.
    """
    try:
        from importlib.metadata import version  # noqa: PLC0415

        return version("yt-dlp")
    except Exception:
        return None


def apply_update() -> None:
    """Prepend the cached update directory to sys.path.

    Must be called before any yt_dlp import. No network I/O — returns instantly.
    Does nothing if no update has been downloaded yet, or if the bundled yt-dlp
    is already as new as the cached one (e.g. after an app rebuild).
    """
    cached = _cached_version()
    if cached is None or not (_UPDATE_DIR / "yt_dlp" / "__init__.py").exists():
        return
    bundled = _bundled_version()
    if bundled is None or _normalise(cached) > _normalise(bundled):
        site = str(_UPDATE_DIR)
        if site not in sys.path:
            sys.path.insert(0, site)


def start_background_update() -> None:
    """Spawn a daemon thread that silently downloads the latest yt-dlp from PyPI.

    The update is extracted to Application Support and is picked up by any sync
    worker started after it finishes. Never blocks the UI or raises exceptions
    to the caller.
    """
    global _thread
    if is_update_running():
        return
    _thread = threading.Thread(
        target=_check_and_update, daemon=True, name="yt-dlp-updater"
    )
    _thread.start()


def is_update_running() -> bool:
    """True while the background update check/download is still in progress."""
    return _thread is not None and _thread.is_alive()


def _ssl_context() -> ssl.SSLContext:
    # The bundled OpenSSL may look for CA certs at a build-machine path (e.g.
    # Homebrew's) that doesn't exist on other Macs, so prefer certifi's bundle.
    try:
        import certifi  # noqa: PLC0415

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _log(message: str) -> None:
    try:
        _UPDATE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        (_UPDATE_DIR / "update.log").write_text(f"{stamp} {message}\n")
    except OSError:
        pass


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
            _log("skipped: could not determine running yt-dlp version")
            return
        ctx = _ssl_context()

        # Fetch latest metadata from PyPI.
        req = urllib.request.Request(
            "https://pypi.org/pypi/yt-dlp/json",
            headers={"User-Agent": "TuneSync-yt-dlp-updater/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data: dict = json.loads(resp.read())

        latest: str = data["info"]["version"]

        # Only ever upgrade: a dev build installed from git master can be newer
        # than the latest PyPI release. Also skip if the cache already holds
        # this version (avoids re-downloading across restarts).
        if _normalise(latest) <= _normalise(running):
            _log(f"up to date: running {running}, latest {latest}")
            return
        cached = _cached_version()
        if cached and _normalise(latest) <= _normalise(cached):
            _log(f"up to date: cached {cached}, latest {latest}")
            return

        # Find the pure-Python wheel (no platform-specific binary needed).
        wheel_url: str | None = None
        for file_info in data.get("urls", []):
            if file_info["filename"].endswith("-py3-none-any.whl"):
                wheel_url = file_info["url"]
                break
        if not wheel_url:
            _log(f"skipped: no pure-Python wheel for {latest}")
            return

        _UPDATE_DIR.mkdir(parents=True, exist_ok=True)

        # Download wheel to a temp path.
        tmp_wheel = _UPDATE_DIR / f"_download-{latest}.whl"
        with urllib.request.urlopen(wheel_url, timeout=120, context=ctx) as resp:
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

        _log(f"updated: {running} -> {latest}")

    except Exception as e:
        # Never crash the app due to an update failure.
        _log(f"failed: {e!r}")
