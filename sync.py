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

SYNCED_FILE = Path("synced.txt")


# ---------------------------
# synced.txt parsing
# ---------------------------

def _extract_playlist_id(token: str) -> str:
    token = token.strip()
    if not token:
        return ""
    # Full URL
    if "open.spotify.com/playlist/" in token:
        return token.split("/")[-1].split("?")[0]
    # Spotify URI
    if token.startswith("spotify:playlist:"):
        return token.split(":")[-1]
    # Plain ID
    return token


def _parse_synced_line(line: str) -> Optional[Tuple[str, Optional[str]]]:
    """
    Returns (playlist_id, alias) or None if the line should be ignored.
    Supports:
      - "<url_or_id>"
      - "<url_or_id> # alias"
      - "alias = <url_or_id>"
      - "alias | <url_or_id>"
    """
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None

    alias = None
    # Allow trailing "# alias"
    if "#" in raw:
        left, comment = raw.split("#", 1)
        raw = left.strip()
        alias = (comment or "").strip() or None

    # Allow "alias = url_or_id" or "alias | url_or_id"
    if ("=" in raw) or ("|" in raw):
        if "=" in raw:
            name, right = raw.split("=", 1)
        else:
            name, right = raw.split("|", 1)
        alias = alias or name.strip() or None
        pid = _extract_playlist_id(right.strip())
    else:
        pid = _extract_playlist_id(raw)

    if not pid:
        return None
    return (pid, alias)


def read_synced() -> List[str]:
    """Return playlist IDs from synced.txt."""
    if not SYNCED_FILE.exists():
        return []
    ids: List[str] = []
    for line in SYNCED_FILE.read_text().splitlines():
        parsed = _parse_synced_line(line)
        if not parsed:
            continue
        pid, _ = parsed
        ids.append(pid)
    # de-dupe while preserving order
    seen = set()
    uniq = []
    for pid in ids:
        if pid not in seen:
            uniq.append(pid)
            seen.add(pid)
    return uniq


def get_synced_alias_map() -> Dict[str, str]:
    """
    Return {playlist_id: alias} for lines where you provided a friendly name.
    Exporters (XML/M3U) can use this for display names.
    """
    out: Dict[str, str] = {}
    if not SYNCED_FILE.exists():
        return out
    for line in SYNCED_FILE.read_text().splitlines():
        parsed = _parse_synced_line(line)
        if not parsed:
            continue
        pid, alias = parsed
        if alias:
            out[pid] = alias
    return out


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
    """Delete playlists that are no longer listed in synced.txt (cascades links)."""
    if not keep_ids:
        conn.execute("DELETE FROM playlists")
        return
    qmarks = ",".join("?" for _ in keep_ids)
    conn.execute(f"DELETE FROM playlists WHERE id NOT IN ({qmarks})", tuple(keep_ids))


# ---------------------------
# Main sync
# ---------------------------

def sync() -> Tuple[List[dict], List[dict], bool]:
    """
    1) Read desired playlists from synced.txt (with aliases supported)
    2) Remove stale playlists from DB
    3) For each desired playlist:
         - upsert playlist row
         - if snapshot_id changed (or new), fetch tracks, upsert tracks, refresh links
         - if same snapshot_id, skip track fetch to save API calls
    4) Return (missing_tracks, orphaned_tracks, playlists_changed)
    """
    conn = get_conn()
    desired_ids = set(read_synced())

    # If no desired playlists, clear playlists and finish
    if not desired_ids:
        _delete_stale_playlists(conn, set())
        conn.commit()
        missing = []
        orphaned = get_orphaned_tracks(conn)
        print("✅ Sync complete (no desired playlists listed).")
        return (missing, orphaned, False)

    # Prune anything not listed
    _delete_stale_playlists(conn, desired_ids)

    print(f"🔄 Syncing {len(desired_ids)} playlists from Spotify...")
    
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
        for t in tracks:
            upsert_track(conn, t)
            # store added_at so exports can preserve Spotify order
            link_playlist_track(conn, pid, t["id"], added_at=t.get("added_at"))
        # (If you prefer to clear+bulk re-link, _refresh_playlist_links(conn, pid, tracks) also does this)
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
