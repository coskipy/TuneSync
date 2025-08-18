# playlist_export.py
from __future__ import annotations
from pathlib import Path
import os
import re
from typing import Optional, Dict

from db import get_conn, resolve_path


def _sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def export_all_m3u(
    download_root: Path,
    dest_dir: Optional[Path] = None,
    *,
    alias_map: Optional[Dict[str, str]] = None,
    purge_existing: bool = False,
) -> int:
    """
    Create one .m3u8 per playlist with paths RELATIVE to the M3U directory.
    Rekordbox (and most players) import these without duplicating files.

    - alias_map: {playlist_id: friendly_name} from sync.get_synced_alias_map()
    - purge_existing: if True, remove old .m3u8 in dest before writing new ones

    Returns number of playlists exported.
    """
    conn = get_conn()
    dest = dest_dir or (download_root / "Playlists")
    dest.mkdir(parents=True, exist_ok=True)

    if purge_existing:
        for p in dest.glob("*.m3u8"):
            try:
                p.unlink()
            except Exception:
                pass

    # Fetch playlists and sort by display name (alias > Spotify name)
    playlists = conn.execute("SELECT id, name FROM playlists").fetchall()

    def _display_name(pl_row) -> str:
        return (alias_map.get(pl_row["id"]) if alias_map else pl_row["name"]) or pl_row["name"]

    playlists_sorted = sorted(playlists, key=_display_name)

    count = 0

    for pl in playlists_sorted:
        # Only include tracks that have a file on disk (JOIN files)
        rows = conn.execute("""
            SELECT t.id, t.name, t.artist, t.duration_ms, f.file_path, pt.added_at
            FROM playlist_tracks pt
            JOIN tracks t ON t.id = pt.track_id
            JOIN files  f ON f.track_id = t.id
            WHERE pt.playlist_id = ?
            ORDER BY
              CASE WHEN pt.added_at IS NULL THEN 1 ELSE 0 END,
              pt.added_at,
              t.artist, t.name
        """, (pl["id"],)).fetchall()

        display = _display_name(pl)
        safe_name = _sanitize_filename(display)
        out_path = dest / f"{safe_name}.m3u8"

        seen: set[str] = set()  # avoid dup entries within a playlist
        with out_path.open("w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for r in rows:
                if r["id"] in seen:
                    continue
                seen.add(r["id"])

                # Resolve absolute path from stored (DB) relative path
                abs_file = resolve_path(download_root, r["file_path"])
                if not abs_file.exists():
                    # file missing on disk; skip it gracefully
                    continue

                # Make path relative to the M3U directory, normalize to forward slashes
                rel_for_m3u = os.path.relpath(abs_file, start=dest).replace("\\", "/")

                secs = int((r["duration_ms"] or 0) / 1000)
                f.write(f"#EXTINF:{secs},{r['artist']} - {r['name']}\n")
                f.write(f"{rel_for_m3u}\n")

        count += 1

    return count
