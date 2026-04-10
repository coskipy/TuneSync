# sync.py
from __future__ import annotations
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import time
import os

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

    debug = (os.getenv("TUNESYNC_DEBUG") or "").strip().lower() in {"1", "true", "yes"}

    # Freshness checks are a best-effort workaround for Spotify snapshot lag right after edits.
    # They must never stall a full sync, especially with many playlists.
    try:
        freshness_retries = int((os.getenv("TUNESYNC_FRESHNESS_RETRIES") or "2").strip() or 2)
    except Exception:
        freshness_retries = 2
    freshness_retries = max(0, min(freshness_retries, 5))

    try:
        freshness_sleep_s = float((os.getenv("TUNESYNC_FRESHNESS_SLEEP_S") or "0.2").strip() or 0.2)
    except Exception:
        freshness_sleep_s = 0.2
    freshness_sleep_s = max(0.0, min(freshness_sleep_s, 2.0))

    try:
        freshness_budget_s = float((os.getenv("TUNESYNC_FRESHNESS_BUDGET_S") or "6").strip() or 6.0)
    except Exception:
        freshness_budget_s = 6.0
    freshness_budget_s = max(0.0, min(freshness_budget_s, 30.0))

    freshness_budget_start = time.monotonic()

    def dbg(msg: str) -> None:
        if not debug:
            return
        try:
            print(f"[sync] {msg}", flush=True)
        except Exception:
            pass

    # If no desired playlists, do nothing.
    if not desired_ids:
        missing = []
        orphaned = get_orphaned_tracks(conn)
        print("✅ Sync complete (no playlists enabled).")
        return (missing, orphaned, False)

    print(f"🔄 Syncing {len(desired_ids)} enabled playlists from Spotify...", flush=True)
    dbg("begin")
    
    total = len(desired_ids)
    
    # Fetch all playlist metadata in one batch call (much faster!)
    t0 = time.monotonic()
    dbg("fetch metadata batch: start")
    all_metadata = SpotifyClient.get_playlists_metadata_batch(list(desired_ids))
    dbg(f"fetch metadata batch: done ({(time.monotonic() - t0):.2f}s) got={len(all_metadata)}")
    
    # Upsert playlists + (conditionally) their tracks
    updated = 0
    skipped = 0
    
    for idx, pid in enumerate(desired_ids, 1):
        meta = all_metadata.get(pid)
        if not meta:
            print(f"⚠️  Playlist {pid} not found, skipping")
            continue

        dbg(f"playlist {idx}/{total} id={pid} name={meta.get('name')!r}")
            
        prev_snapshot = _get_db_playlist_snapshot(conn, pid)

        # Spotify snapshot IDs can lag briefly right after playlist edits.
        # If the batch metadata says "unchanged", verify freshness with a few
        # direct metadata reads before deciding to skip.
        if prev_snapshot and meta.get("snapshot_id") == prev_snapshot:
            refreshed = meta
            if freshness_retries <= 0 or freshness_budget_s <= 0:
                dbg(f"playlist {pid}: freshness checks disabled")
            elif (time.monotonic() - freshness_budget_start) >= freshness_budget_s:
                dbg(f"playlist {pid}: freshness budget exhausted; skipping")
            else:
                for _attempt in range(freshness_retries):
                    if (time.monotonic() - freshness_budget_start) >= freshness_budget_s:
                        dbg(f"playlist {pid}: freshness budget exhausted mid-check; stopping")
                        break
                try:
                    dbg(f"playlist {pid}: snapshot unchanged; freshness check attempt={_attempt + 1}")
                    fresh = SpotifyClient.get_playlist_metadata(pid, silent=True)
                    if fresh:
                        refreshed = fresh
                    if refreshed.get("snapshot_id") != prev_snapshot:
                        break
                except Exception:
                    pass
                if freshness_sleep_s > 0:
                    time.sleep(freshness_sleep_s)
            meta = refreshed

        upsert_playlist(conn, meta)

        # If snapshot hasn't changed, skip heavy track fetch
        if prev_snapshot and meta.get("snapshot_id") == prev_snapshot:
            skipped += 1
            continue

        # Snapshot changed or new playlist: refresh contents
        playlist_name = meta.get("name", "Unknown")
        dbg(f"playlist {pid}: fetch tracks start name={playlist_name!r}")
        t1 = time.monotonic()
        tracks = SpotifyClient.get_playlist_tracks(pid)
        dbg(f"playlist {pid}: fetch tracks done ({(time.monotonic() - t1):.2f}s) tracks={len(tracks)}")

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

    print(f"📋 Checked {total} playlists (updated: {updated}, unchanged: {skipped})", flush=True)
    dbg("commit")
    conn.commit()
    dbg("post-commit")

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
