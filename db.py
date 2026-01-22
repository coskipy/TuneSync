# db.py
import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager
import re


_BASE_DIR = Path(__file__).resolve().parent


def _path_from_env(var: str, default: Path) -> Path:
    v = (os.getenv(var) or "").strip()
    if not v:
        return default
    return Path(v).expanduser().resolve()


# Default to a stable location (repo root) instead of the current working directory.
DB_PATH = _path_from_env("LIGHTSYNC_DB_PATH", _BASE_DIR / "capsize.sqlite3")
LEGACY_SYNCED_TXT_PATH = _path_from_env("LIGHTSYNC_SYNCED_TXT_PATH", _BASE_DIR / "synced.txt")


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
    CREATE TABLE IF NOT EXISTS app_state (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

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
        sync_enabled_added = _ensure_column(conn, "playlists", "sync_enabled", "INTEGER DEFAULT 1")
        _ensure_column(conn, "tracks", "cover_url", "TEXT")

        # One-time, best-effort migration: if we just added sync_enabled and a legacy
        # synced.txt exists, mirror its selections into the DB. Normal operation does
        # not depend on synced.txt.
        # This makes the DB the source of truth while keeping existing users' selections.
        if sync_enabled_added:
            try:
                synced_path = LEGACY_SYNCED_TXT_PATH
                if synced_path.exists():
                    ids: list[str] = []

                    def extract_id(token: str) -> str:
                        token = (token or "").strip()
                        if not token:
                            return ""
                        if "open.spotify.com/playlist/" in token:
                            return token.split("/playlist/", 1)[1].split("?", 1)[0].split("/", 1)[0]
                        if token.startswith("spotify:playlist:"):
                            return token.split(":")[-1]
                        return token

                    for line in synced_path.read_text().splitlines():
                        raw = (line or "").strip()
                        if not raw or raw.startswith("#"):
                            continue
                        if "#" in raw:
                            raw = raw.split("#", 1)[0].strip()
                        if ("=" in raw) or ("|" in raw):
                            right = raw.split("=", 1)[1] if "=" in raw else raw.split("|", 1)[1]
                            token = right.strip()
                        else:
                            token = raw
                        pid = extract_id(token)
                        if pid:
                            ids.append(pid)

                    # de-dupe while preserving order
                    seen: set[str] = set()
                    uniq: list[str] = []
                    for pid in ids:
                        if pid in seen:
                            continue
                        uniq.append(pid)
                        seen.add(pid)

                    if uniq:
                        # synced.txt was previously source of truth; mirror it.
                        conn.execute("UPDATE playlists SET sync_enabled = 0")
                        for pid in uniq:
                            conn.execute(
                                "INSERT OR IGNORE INTO playlists (id, name, sync_enabled) VALUES (?, ?, 1)",
                                (pid, pid),
                            )
                            conn.execute("UPDATE playlists SET sync_enabled = 1 WHERE id = ?", (pid,))
            except Exception:
                pass

        _import_legacy_synced_txt_if_needed(conn)

        # Backfill created_at for existing rows (best-effort).
        try:
            conn.execute(
                "UPDATE playlists SET created_at = COALESCE(created_at, updated_at, CURRENT_TIMESTAMP)"
            )
        except Exception:
            pass


def _app_state_get(conn: sqlite3.Connection, key: str) -> str | None:
    try:
        row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _app_state_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
        (key, value),
    )


def _import_legacy_synced_txt_if_needed(conn: sqlite3.Connection) -> None:
    """One-time import from synced.txt if DB has no playlists.

    This is intentionally conservative: it only runs when the playlists table is empty.
    """
    try:
        if _app_state_get(conn, "migrated_synced_txt") == "1":
            return

        playlists_count = conn.execute("SELECT COUNT(*) FROM playlists").fetchone()[0]
        if int(playlists_count or 0) != 0:
            return

        synced_path = LEGACY_SYNCED_TXT_PATH
        if not synced_path.exists():
            return

        id_re = re.compile(r"^[A-Za-z0-9]{22}$")

        def extract_id(token: str) -> str:
            token = (token or "").strip()
            if not token:
                return ""
            if "open.spotify.com/playlist/" in token:
                token = token.split("/playlist/", 1)[1]
                token = token.split("?", 1)[0]
                token = token.split("/", 1)[0]
            elif token.startswith("spotify:playlist:"):
                token = token.split(":")[-1]
            return token.strip()

        to_upsert: list[tuple[str, str]] = []
        for line in synced_path.read_text().splitlines():
            raw = (line or "").strip()
            if not raw or raw.startswith("#"):
                continue
            if "#" in raw:
                raw = raw.split("#", 1)[0].strip()
            name = ""
            token = ""
            if "=" in raw:
                left, right = raw.split("=", 1)
                name = left.strip()
                token = right.strip()
            else:
                token = raw

            pid = extract_id(token)
            if not pid or not id_re.match(pid):
                continue
            if not name:
                name = pid
            to_upsert.append((pid, name))

        if not to_upsert:
            return

        for pid, name in to_upsert:
            conn.execute(
                "INSERT INTO playlists (id, name, sync_enabled) VALUES (?, ?, 1) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, sync_enabled=1, updated_at=CURRENT_TIMESTAMP",
                (pid, name),
            )

        _app_state_set(conn, "migrated_synced_txt", "1")
    except Exception:
        return


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_def: str) -> bool:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column in cols:
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")
    return True


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
    # Only consider playlists with sync enabled.
    return conn.execute(
        """
        SELECT DISTINCT t.id, t.name, t.artist, t.album, t.duration_ms, t.release_date, t.isrc
        FROM tracks t
        JOIN playlist_tracks pt ON pt.track_id = t.id
        JOIN playlists p ON p.id = pt.playlist_id
        WHERE COALESCE(p.sync_enabled, 1) = 1
          AND t.id NOT IN (SELECT track_id FROM files)
          AND (t.duration_ms IS NULL OR t.duration_ms >= ?)
        ORDER BY t.artist, t.name
        """,
        (min_duration_ms,),
    ).fetchall()


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


