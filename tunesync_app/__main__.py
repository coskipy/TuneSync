from __future__ import annotations

import sys

# Apply any previously downloaded yt-dlp update before importing anything that
# uses yt_dlp. This must happen first so sys.path is correct when the worker
# subprocess does `from yt_dlp import YoutubeDL`.
# Wrapped in try/except so a broken updater module never prevents the app launching.
try:
    from tunesync_app.yt_dlp_updater import apply_update, start_background_update

    apply_update()
except Exception:

    def apply_update() -> None:  # type: ignore[misc]
        pass

    def start_background_update() -> None:  # type: ignore[misc]
        pass


def _run_sync_worker() -> int:
    """Run the backend sync pipeline in 'worker' mode.

    This is used by the GUI to spawn a subprocess that streams progress to stdout.
    In a packaged .app, the GUI spawns the app binary with `--sync-worker`.
    """
    import runpy

    # Execute the existing top-level CLI pipeline (repo root `main.py`).
    # NOTE: PyInstaller needs to include the `main` module (handled by our spec).
    runpy.run_module("main", run_name="__main__")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--sync-worker" in argv:
        return _run_sync_worker()

    # GUI mode: kick off background yt-dlp update check (daemon thread, non-blocking).
    start_background_update()

    from tunesync_app.main import main as gui_main

    return int(gui_main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
