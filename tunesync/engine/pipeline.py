from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from tunesync.util.events import Event, Phase


EventSink = Callable[[Event], None]


def _emit(sink: Optional[EventSink], event: Event) -> None:
    if sink is not None:
        sink(event)


def run_sync(
    *,
    download_root: Path,
    max_workers: int = 8,
    debug_search: bool = False,
    ignore_dirs: Optional[set[str]] = None,
    event_sink: Optional[EventSink] = None,
) -> dict:
    """Run the existing TuneSync pipeline with optional event emission.

    This is intentionally a thin wrapper around the current flat modules so we
    can iterate on UI without a risky refactor.

    Returns a summary dict suitable for UI rendering.
    """

    ignore_dirs = ignore_dirs or {"old"}

    # Import existing modules (keeps current code working)
    from db import init_db, get_conn, get_missing_tracks, attach_file
    from sync import sync, get_synced_alias_map
    from rescan import rescan_existing_files
    from downloader import download_missing_batch
    from tagger import tag_tracks_in_db
    from rekordbox_export import export_rekordbox_xml

    summary: dict = {
        "successes": 0,
        "failures": 0,
        "transcoded": 0,
        "tagged": 0,
        "exported_xml": None,
        "errors": [],
    }

    try:
        _emit(event_sink, Event(type="phase_started", phase=Phase.INIT_DB, message="Initializing database"))
        init_db()
        _emit(event_sink, Event(type="phase_done", phase=Phase.INIT_DB))
    except Exception as e:
        _emit(event_sink, Event(type="error", phase=Phase.INIT_DB, message=str(e)))
        summary["errors"].append({"phase": Phase.INIT_DB.value, "error": str(e)})
        return summary

    # Spotify sync
    playlists_changed = False
    missing = []
    try:
        _emit(event_sink, Event(type="phase_started", phase=Phase.SPOTIFY_SYNC, message="Syncing playlists from Spotify"))
        missing, _orphaned, playlists_changed = sync()
        _emit(event_sink, Event(type="phase_done", phase=Phase.SPOTIFY_SYNC))
    except Exception as e:
        _emit(event_sink, Event(type="warning", phase=Phase.SPOTIFY_SYNC, message=f"Spotify sync failed; using existing DB: {e}"))
        summary["errors"].append({"phase": Phase.SPOTIFY_SYNC.value, "error": str(e)})
        try:
            conn = get_conn()
            missing = get_missing_tracks(conn)
            playlists_changed = True
        except Exception as inner:
            _emit(event_sink, Event(type="error", phase=Phase.SPOTIFY_SYNC, message=str(inner)))
            summary["errors"].append({"phase": Phase.SPOTIFY_SYNC.value, "error": str(inner)})
            return summary

    # Rescan
    try:
        _emit(event_sink, Event(type="phase_started", phase=Phase.RESCAN, message="Scanning local library"))
        if playlists_changed or missing:
            stats = rescan_existing_files(download_root, ignore_dirs=ignore_dirs)
            _emit(event_sink, Event(type="log", phase=Phase.RESCAN, message="Rescan complete", data=stats))
        _emit(event_sink, Event(type="phase_done", phase=Phase.RESCAN))
    except Exception as e:
        _emit(event_sink, Event(type="warning", phase=Phase.RESCAN, message=str(e)))
        summary["errors"].append({"phase": Phase.RESCAN.value, "error": str(e)})

    # Recompute missing
    conn = get_conn()
    still_missing = get_missing_tracks(conn)
    total = len(still_missing)

    # Download
    failures = []
    newly_downloaded_track_ids: list[str] = []

    if total:
        _emit(event_sink, Event(type="phase_started", phase=Phase.DOWNLOAD, message=f"Downloading {total} tracks"))
        try:
            results = download_missing_batch(
                still_missing,
                download_root,
                aac_kbps=192,
                progress=False,  # UI owns progress
                max_workers=max_workers,
                debug=debug_search,
            )
            for idx, r in enumerate(results, 1):
                _emit(event_sink, Event(type="progress", phase=Phase.DOWNLOAD, current=idx, total=total))
                if r and r.ok:
                    try:
                        rel = r.final_path.relative_to(download_root)
                        if not getattr(r, "already_existed", False):
                            attach_file(conn, r.track_id, rel, download_root)
                            newly_downloaded_track_ids.append(r.track_id)
                            if r.transcoded:
                                summary["transcoded"] += 1
                        summary["successes"] += 1
                    except Exception as attach_err:
                        failures.append({"track": getattr(r, "title", None), "error": str(attach_err)})
                else:
                    failures.append({"track": getattr(r, "title", None), "error": getattr(r, "error", "unknown")})

            conn.commit()
        except Exception as e:
            failures.append({"track": None, "error": str(e)})
            summary["errors"].append({"phase": Phase.DOWNLOAD.value, "error": str(e)})
        _emit(event_sink, Event(type="phase_done", phase=Phase.DOWNLOAD))

    summary["failures"] = len(failures)

    # Tag
    try:
        _emit(event_sink, Event(type="phase_started", phase=Phase.TAG, message="Tagging newly downloaded files"))
        if newly_downloaded_track_ids:
            tag_stats = tag_tracks_in_db(download_root, track_ids=newly_downloaded_track_ids, progress=False)
            summary["tagged"] = int(tag_stats.get("tagged", 0))
        _emit(event_sink, Event(type="phase_done", phase=Phase.TAG))
    except Exception as e:
        _emit(event_sink, Event(type="warning", phase=Phase.TAG, message=str(e)))
        summary["errors"].append({"phase": Phase.TAG.value, "error": str(e)})

    # Export
    try:
        _emit(event_sink, Event(type="phase_started", phase=Phase.EXPORT, message="Exporting Rekordbox XML"))
        alias_map = get_synced_alias_map()
        xml_path = export_rekordbox_xml(download_root, out_xml=None, alias_map=alias_map)
        summary["exported_xml"] = str(xml_path)
        _emit(event_sink, Event(type="phase_done", phase=Phase.EXPORT, message="Exported Rekordbox XML"))
    except Exception as e:
        _emit(event_sink, Event(type="warning", phase=Phase.EXPORT, message=str(e)))
        summary["errors"].append({"phase": Phase.EXPORT.value, "error": str(e)})

    if failures:
        _emit(event_sink, Event(type="log", message="Some downloads failed", data={"failures": failures}))

    return summary
