# db.py
import os
import time
import sqlite3
from pathlib import Path
from typing import Any, Iterable

DB_PATH = Path("capsize.sqlite3")

# ---------------------------
# Connection management
# ---------------------------

def get_conn():
    """
    Return a SQLite connection configured for fewer 'database is locked' issues.
    - WAL journal (better concurrency)
    - busy_timeout so SQLite waits for locks
    - connect(timeout=...) so initial acquisition waits too
    - synchronous=NORMAL (safer with WAL; reduces fs churn)
    """
    # NOTE: isolation_level left as default (explicit commits still work).
    # If you want autocommit, pass isolation_level=None and remove explicit commits.
    conn = sqlite3.connect(DB_PATH, timeout=8.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=8000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ---------------------------
# Internal retry helpers
# ---------------------------

def _retryable(e: Exception) -> bool:
    msg = str(e).lower()
    return isinstance(e, sqlite3.OperationalError) and ("locked" in msg or "busy" in msg)

def _exec_with_retry(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = (), *, tries: int = 5, base_sleep: float = 0.18):
    """
    Execute a single statement with small exponential-backoff retries
    when the DB is briefly locked/busy.
    """
    for i in range(tries):
        try:
            return conn.execute(sql, params)
        except Exception as e:
            if _retryable(e) and i < tries - 1:
                time.sleep(base_sleep * (2 ** i))  # ~0.18s → ~2.9s
                continue
            raise

def _executescript_with_retry(conn: sqlite3.Connection, script: str, *, tries: int = 5, base_sleep: float = 0.18):
    for i in range(tries):
        try:
            return conn.executescript(script)
        except Exception as e:
            if _retryable(e) and i < tries - 1:
                time.sleep(base_sleep * (2 ** i))
                continue
            raise

def commit_with_retry(conn: sqlite3.Connection, *, tries: int = 5, base_sleep: float = 0.18):
    """
    Optional helper: commit with retries. You don't have to call this if your
    callers already do conn.commit(); leaving it here for convenience.
    """
    for i in range(tries):
        try:
            return conn.commit()
        except Exception as e:
            if _retryable(e) and i < tries - 1:
                time.sleep(base_sleep * (2 ** i))
                continue
            raise


# ---------------------------
# Schema
# ---------------------------

def init_db():
    """Create tables if they don't already exist."""
    schema = """
    CREATE TABLE IF NOT EXISTS playlists (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        snapshot_id TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS tracks (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        artist TEXT NOT NULL,
        album TEXT,
        duration_ms INTEGER
    );

    CREATE TABLE IF NOT EXISTS playlist_tracks (
        playlist_id TEXT NOT NULL,
        track_id TEXT NOT NULL,
        added_at TEXT,
        PRIMARY KEY (playlist_id, track_id),
        FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
        FOREIGN KEY (track_id)   REFERENCES tracks(id)    ON DELETE CASCADE
    );

    /* Store RELATIVE paths from the download root, not absolute paths */
    CREATE TABLE IF NOT EXISTS files (
        track_id TEXT PRIMARY KEY,
        file_path TEXT NOT NULL,
        downloaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
    );

    /* Cache for YouTube/Source lookups so we skip re-searching */
    CREATE TABLE IF NOT EXISTS sources (
        track_id TEXT PRIMARY KEY,
        url TEXT NOT NULL,
        title TEXT,
        uploader TEXT,
        duration_sec INTEGER,
        checked_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
    );

    /* Helpful indexes */
    CREATE INDEX IF NOT EXISTS idx_tracks_artist_name ON tracks(artist, name);
    CREATE INDEX IF NOT EXISTS idx_pt_playlist        ON playlist_tracks(playlist_id);
    CREATE INDEX IF NOT EXISTS idx_pt_track           ON playlist_tracks(track_id);
    CREATE INDEX IF NOT EXISTS idx_files_path         ON files(file_path);
    """
    with get_conn() as conn:
        _executescript_with_retry(conn, schema)
        # In a 'with' block commit occurs automatically (context manager),
        # but we'll be explicit in case of older Python versions.
        commit_with_retry(conn)


# ---------------------------
# Playlist operations
# ---------------------------

def upsert_playlist(conn, playlist):
    """Insert or update a playlist row."""
    _exec_with_retry(conn, """
        INSERT INTO playlists (id, name, snapshot_id)
        VALUES (?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name,
          snapshot_id=excluded.snapshot_id,
          updated_at=CURRENT_TIMESTAMP
    """, (playlist["id"], playlist["name"], playlist.get("snapshot_id")))


# ---------------------------
# Track operations
# ---------------------------

def upsert_track(conn, track):
    """Insert or update a track row."""
    _exec_with_retry(conn, """
        INSERT INTO tracks (id, name, artist, album, duration_ms)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name,
          artist=excluded.artist,
          album=excluded.album,
          duration_ms=excluded.duration_ms
    """, (track["id"], track["name"], track["artist"], track.get("album"),
          track.get("duration_ms")))


def link_playlist_track(conn, playlist_id, track_id, added_at=None):
    """Link a track to a playlist (ignores duplicates)."""
    _exec_with_retry(conn, """
        INSERT OR IGNORE INTO playlist_tracks (playlist_id, track_id, added_at)
        VALUES (?, ?, ?)
    """, (playlist_id, track_id, added_at))


# ---------------------------
# File path handling (RELATIVE)
# ---------------------------

def _to_relative(download_root: Path, file_path: Path) -> str:
    """Convert a path to be relative to download_root."""
    try:
        rel = file_path if not file_path.is_absolute() else file_path.relative_to(download_root)
    except ValueError:
        rel = file_path.name
    return rel.as_posix() if isinstance(rel, Path) else str(rel)


def resolve_path(download_root: Path, stored_path: str) -> Path:
    """Turn a stored relative path into an absolute path under download_root."""
    return download_root / Path(stored_path)


def attach_file(conn, track_id: str, file_path: Path, download_root: Path | None = None):
    """Record that a track has a file at a given path (store RELATIVE path)."""
    if download_root is None:
        root_env = os.getenv("DOWNLOAD_ROOT")
        download_root = Path(root_env) if root_env else None

    if download_root is not None:
        rel = Path(_to_relative(download_root, Path(file_path)))
    else:
        rel = Path(file_path.name) if Path(file_path).is_absolute() else Path(file_path)

    _exec_with_retry(conn, """
        INSERT INTO files (track_id, file_path)
        VALUES (?, ?)
        ON CONFLICT(track_id) DO UPDATE SET
          file_path=excluded.file_path,
          downloaded_at=CURRENT_TIMESTAMP
    """, (track_id, rel.as_posix()))


def get_missing_tracks(conn):
    """Return all DISTINCT tracks that exist in playlists but are missing files."""
    return conn.execute("""
        SELECT DISTINCT t.id, t.name, t.artist, t.album, t.duration_ms
        FROM tracks t
        JOIN playlist_tracks pt ON pt.track_id = t.id
        WHERE t.id NOT IN (SELECT track_id FROM files)
        ORDER BY t.artist, t.name
    """).fetchall()


def get_orphaned_tracks(conn):
    """Return tracks that no longer belong to any playlist."""
    return conn.execute("""
        SELECT t.id, t.name, t.artist
        FROM tracks t
        LEFT JOIN playlist_tracks pt ON pt.track_id = t.id
        WHERE pt.track_id IS NULL
    """).fetchall()


# ---------------------------
# Migration helpers
# ---------------------------

def migrate_files_to_relative(conn, download_root: Path) -> int:
    """Convert any absolute file_path entries to paths relative to download_root."""
    rows = conn.execute("SELECT track_id, file_path FROM files").fetchall()
    updated = 0
    for r in rows:
        stored = r["file_path"]
        p = Path(stored)
        needs_update = p.is_absolute() or stored.startswith(download_root.as_posix())
        if needs_update:
            rel = _to_relative(download_root, p)
            if rel != stored:
                _exec_with_retry(conn, "UPDATE files SET file_path = ? WHERE track_id = ?", (rel, r["track_id"]))
                updated += 1
    commit_with_retry(conn)
    return updated


def get_file_path_for_track(conn, track_id: str, download_root: Path) -> Path | None:
    """Resolve the stored relative path for a track into an absolute path."""
    row = conn.execute("SELECT file_path FROM files WHERE track_id = ?", (track_id,)).fetchone()
    if not row:
        return None
    return resolve_path(download_root, row["file_path"])


# ---------------------------
# Source (search) cache
# ---------------------------

def get_cached_source(conn, track_id: str):
    return conn.execute(
        "SELECT url, title, uploader, duration_sec FROM sources WHERE track_id = ?",
        (track_id,)
    ).fetchone()

def upsert_source(conn, track_id: str, url: str, *, title: str | None = None, uploader: str | None = None, duration_sec: int | None = None):
    _exec_with_retry(conn, """
        INSERT INTO sources (track_id, url, title, uploader, duration_sec)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(track_id) DO UPDATE SET
          url=excluded.url,
          title=excluded.title,
          uploader=excluded.uploader,
          duration_sec=excluded.duration_sec,
          checked_at=CURRENT_TIMESTAMP
    """, (track_id, url, title, uploader, duration_sec))

def delete_source_cache(conn, track_id: str):
    _exec_with_retry(conn, "DELETE FROM sources WHERE track_id = ?", (track_id,))
