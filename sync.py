# sync.py
from __future__ import annotations
from pathlib import Path
from typing import List, Tuple, Dict, Optional

from spotify_client import SpotifyClient
from db import (
    get_conn,
    upsert_playlist,
    upsert_track,
    link_playlist_track,
    get_missing_tracks,
    get_orphaned_tracks,
)


def _sync_enabled_playlist_ids(conn) -> List[str]:
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(playlists)").fetchall()]
        if "sync_enabled" in cols:
            rows = conn.execute(
                "SELECT id FROM playlists WHERE COALESCE(sync_enabled, 1) = 1 ORDER BY name"
            ).fetchall()
        else:
            # Back-compat: older DBs treat all playlists as enabled.
            rows = conn.execute("SELECT id FROM playlists ORDER BY name").fetchall()
        return [r["id"] for r in rows]
    except Exception:
        return []


def read_desired_playlist_ids(conn) -> List[str]:
    """Return desired playlist IDs from SQLite (DB is source of truth)."""
    return _sync_enabled_playlist_ids(conn)


def get_synced_alias_map() -> Dict[str, str]:
    """Return {playlist_id: display_name} for enabled playlists."""
    try:
        conn = get_conn()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(playlists)").fetchall()]
        if "sync_enabled" in cols:
            rows = conn.execute(
                "SELECT id, name FROM playlists WHERE COALESCE(sync_enabled, 1) = 1"
            ).fetchall()
            out_db: Dict[str, str] = {}
            for r in rows:
                if r and r["id"] and r["name"]:
                    out_db[r["id"]] = r["name"]
            return out_db
    except Exception:
        return {}


# ---------------------------
# DB helpers
# ---------------------------

def _get_db_playlist_snapshot(conn, playlist_id: str) -> Optional[str]:
    row = conn.execute("SELECT snapshot_id FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
    return row["snapshot_id"] if row else None


def _refresh_playlist_links(conn, playlist_id: str, tracks: List[dict]) -> None:
    """
    Replace playlist_tracks links for a playlist with a fresh set (idempotent).
    Preserves Spotify order via added_at when available.
    """
    conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
    for t in tracks:
        link_playlist_track(conn, playlist_id, t["id"], added_at=t.get("added_at"))


def _delete_stale_playlists(conn, keep_ids: set[str]) -> None:
    """Deprecated: playlists are now kept in DB even when disabled."""
    return


# ---------------------------
# Main sync
# ---------------------------

def sync() -> Tuple[List[dict], List[dict], bool]:
    """
    1) Read desired playlists from SQLite (playlists.sync_enabled)
    2) Sync only enabled playlists (disabled playlists are kept in DB)
    3) For each desired playlist:
         - upsert playlist row
         - if snapshot_id changed (or new), fetch tracks, upsert tracks, refresh links
         - if same snapshot_id, skip track fetch to save API calls
    4) Return (missing_tracks, orphaned_tracks, playlists_changed)
    """
    conn = get_conn()
    desired_ids = set(read_desired_playlist_ids(conn))

    # If no desired playlists, do nothing.
    if not desired_ids:
        missing = []
        orphaned = get_orphaned_tracks(conn)
        print("✅ Sync complete (no playlists enabled).")
        return (missing, orphaned, False)

    print(f"🔄 Syncing {len(desired_ids)} enabled playlists from Spotify...")
    
    total = len(desired_ids)
    
    # Fetch all playlist metadata in one batch call (much faster!)
    all_metadata = SpotifyClient.get_playlists_metadata_batch(list(desired_ids))
    
    # Upsert playlists + (conditionally) their tracks
    updated = 0
    skipped = 0
    
    for idx, pid in enumerate(desired_ids, 1):
        meta = all_metadata.get(pid)
        if not meta:
            print(f"⚠️  Playlist {pid} not found, skipping")
            continue
            
        prev_snapshot = _get_db_playlist_snapshot(conn, pid)
        upsert_playlist(conn, meta)

        # If snapshot hasn't changed, skip heavy track fetch
        if prev_snapshot and meta.get("snapshot_id") == prev_snapshot:
            skipped += 1
            continue

        # Snapshot changed or new playlist: refresh contents
        playlist_name = meta.get("name", "Unknown")
        tracks = SpotifyClient.get_playlist_tracks(pid)

        # Replace links so removed tracks don't linger in DB.
        conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (pid,))
        min_added_at: str | None = None
        for t in tracks:
            upsert_track(conn, t)
            added_at = t.get("added_at")
            # store added_at so exports can preserve Spotify order
            link_playlist_track(conn, pid, t["id"], added_at=added_at)
            if added_at:
                if min_added_at is None or added_at < min_added_at:
                    min_added_at = added_at

        # Best-effort: treat earliest "added_at" as a proxy for playlist creation.
        # Only set it once so it stays stable even if early tracks are later removed.
        if min_added_at:
            try:
                conn.execute(
                    "UPDATE playlists SET spotify_created_at = COALESCE(spotify_created_at, ?) WHERE id = ?",
                    (min_added_at, pid),
                )
            except Exception:
                pass
        updated += 1

    print(f"📋 Checked {total} playlists (updated: {updated}, unchanged: {skipped})")
    conn.commit()

    missing_rows = get_missing_tracks(conn)   # tracks referenced by any playlist but no file yet
    orphaned_rows = get_orphaned_tracks(conn) # tracks no longer referenced by any playlist

    playlists_changed = updated > 0  # True if any playlists were updated
    
    return (missing_rows, orphaned_rows, playlists_changed)


if __name__ == "__main__":
    missing, orphaned = sync()
    if missing:
        print("Examples to download:")
        for r in list(missing)[:5]:
            print(f"  - {r['artist']} - {r['name']}")
    if orphaned:
        print("Orphaned examples:")
        for r in list(orphaned)[:5]:
            print(f"  - {r['artist']} - {r['name']}")
