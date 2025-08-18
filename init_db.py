from pathlib import Path
import sqlite3

DB_PATH = Path("capsize.sqlite3")

def init_db():
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
        FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS files (
        track_id TEXT PRIMARY KEY,
        file_path TEXT NOT NULL,
        source_url TEXT,
        format TEXT,
        bitrate_kbps INTEGER,
        downloaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
    );
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(schema)

if __name__ == "__main__":
    init_db()
    print("Database initialized at", DB_PATH)
