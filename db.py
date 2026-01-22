# db.py
import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path("capsize.sqlite3")


def get_conn():
    """Return a SQLite connection with foreign keys enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def get_conn_context():
    """Context manager for database connections (auto-closes on exit)."""
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Create tables if they don't already exist."""
    schema = """
    CREATE TABLE IF NOT EXISTS playlists (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        creator TEXT,
        snapshot_id TEXT,
        image_url TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS tracks (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        artist TEXT NOT NULL,
        album TEXT,
        cover_url TEXT,
        duration_ms INTEGER,
        release_date TEXT,
        isrc TEXT
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

    /* Helpful indexes */
    CREATE INDEX IF NOT EXISTS idx_tracks_artist_name ON tracks(artist, name);
    CREATE INDEX IF NOT EXISTS idx_pt_playlist        ON playlist_tracks(playlist_id);
    CREATE INDEX IF NOT EXISTS idx_pt_track           ON playlist_tracks(track_id);
    CREATE INDEX IF NOT EXISTS idx_files_path         ON files(file_path);
    """
    with get_conn() as conn:
        conn.executescript(schema)

        # Lightweight migration for older DBs.
        _ensure_column(conn, "playlists", "image_url", "TEXT")
        _ensure_column(conn, "playlists", "creator", "TEXT")
        _ensure_column(conn, "playlists", "created_at", "TEXT")
        _ensure_column(conn, "tracks", "cover_url", "TEXT")

        # Backfill created_at for existing rows (best-effort).
        try:
            conn.execute(
                "UPDATE playlists SET created_at = COALESCE(created_at, updated_at, CURRENT_TIMESTAMP)"
            )
        except Exception:
            pass


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_def: str) -> None:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column in cols:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")


# ---------------------------
# Playlist operations
# ---------------------------

def upsert_playlist(conn, playlist):
    """Insert or update a playlist row."""
    conn.execute("""
                                INSERT INTO playlists (id, name, creator, snapshot_id, image_url)
                                VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name,
                    creator=excluded.creator,
          snapshot_id=excluded.snapshot_id,
                    image_url=excluded.image_url,
          updated_at=CURRENT_TIMESTAMP
                """, (playlist["id"], playlist["name"], playlist.get("creator"), playlist.get("snapshot_id"), playlist.get("image_url")))


# ---------------------------
# Track operations
# ---------------------------

def upsert_track(conn, track):
    """Insert or update a track row."""
    conn.execute("""
      INSERT INTO tracks (id, name, artist, album, cover_url, duration_ms, release_date, isrc)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name,
          artist=excluded.artist,
          album=excluded.album,
        cover_url=COALESCE(excluded.cover_url, tracks.cover_url),
        duration_ms=excluded.duration_ms,
        release_date=excluded.release_date,
        isrc=excluded.isrc
    """, (track["id"], track["name"], track["artist"], track.get("album"),
        track.get("cover_url"), track.get("duration_ms"), track.get("release_date"), track.get("isrc")))


def link_playlist_track(conn, playlist_id, track_id, added_at=None):
    """Link a track to a playlist (ignores duplicates)."""
    conn.execute("""
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

    rel = file_path
    if download_root is not None:
        rel = Path(_to_relative(download_root, Path(file_path)))
    else:
        rel = Path(file_path.name) if Path(file_path).is_absolute() else Path(file_path)

    conn.execute("""
        INSERT INTO files (track_id, file_path)
        VALUES (?, ?)
        ON CONFLICT(track_id) DO UPDATE SET
          file_path=excluded.file_path,
          downloaded_at=CURRENT_TIMESTAMP
    """, (track_id, rel.as_posix()))


def get_missing_tracks(conn, min_duration_sec: int = 60):
    """
    Return all DISTINCT tracks that exist in playlists but are missing files.
    
    Args:
        min_duration_sec: Minimum track duration in seconds (default 60).
                         Tracks shorter than this will be excluded (e.g., previews).
                         Set to 0 to include all tracks.
    """
    min_duration_ms = min_duration_sec * 1000
    return conn.execute("""
        SELECT DISTINCT t.id, t.name, t.artist, t.album, t.duration_ms, t.release_date, t.isrc
        FROM tracks t
        JOIN playlist_tracks pt ON pt.track_id = t.id
        WHERE t.id NOT IN (SELECT track_id FROM files)
          AND (t.duration_ms IS NULL OR t.duration_ms >= ?)
        ORDER BY t.artist, t.name
    """, (min_duration_ms,)).fetchall()


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
                conn.execute("UPDATE files SET file_path = ? WHERE track_id = ?", (rel, r["track_id"]))
                updated += 1
    conn.commit()
    return updated


def get_file_path_for_track(conn, track_id: str, download_root: Path) -> Path | None:
    """Resolve the stored relative path for a track into an absolute path."""
    row = conn.execute("SELECT file_path FROM files WHERE track_id = ?", (track_id,)).fetchone()
    if not row:
        return None
    return resolve_path(download_root, row["file_path"])


