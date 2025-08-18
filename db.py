import sqlite3
from pathlib import Path

DB_PATH = Path("db.sqlite3")

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

# example: insert a track
def upsert_track(conn, track):
    conn.execute("""
        INSERT INTO tracks (id, name, artist, album, duration_ms, isrc, explicit)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name,
          artist=excluded.artist,
          album=excluded.album,
          duration_ms=excluded.duration_ms,
          isrc=excluded.isrc,
          explicit=excluded.explicit
    """, (track["id"], track["name"], track["artist"], track.get("album"),
          track.get("duration_ms"), track.get("isrc"), int(track.get("explicit", 0))))
