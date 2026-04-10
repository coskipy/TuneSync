# rescan.py
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Optional, Iterable, Set
import re
import unicodedata

from db import get_conn, attach_file, resolve_path

# Files Rekordbox handles (plus a couple we may transcode from)
AUDIO_EXTS = {"m4a", "mp3", "aac", "wav", "aiff", "flac", "alac", "opus", "webm"}


# ---------------------------
# Normalization helpers
# ---------------------------

_YT_GARBAGE_PAT = re.compile(
    r"""
    \b(official\s*video|official\s*audio|audio\s*only|lyric[s]?|lyrics\s*video|
       visualizer|remaster(?:ed)?\s*\d{2,4}?|HD|4K|HQ|MV|PV)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

def _sanitize(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    return re.sub(r"\s+", " ", name).strip()

def _norm_base(stem: str) -> str:
    """Normalize a filename stem for matching."""
    # Unicode normalize and lowercase
    s = unicodedata.normalize("NFKC", stem)
    s = _sanitize(s).lower()

    # Remove common YT fluff
    s = _YT_GARBAGE_PAT.sub(" ", s)

    # '&' tends to appear interchangeably with 'and'
    s = s.replace("&", " and ")

    # Kill brackets/dashes/underscores/punctuation; keep alnum+space
    s = re.sub(r"[\[\]\(\)\-_\.\,]+", " ", s)
    s = re.sub(r"[^a-z0-9\s]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ---------------------------
# Metadata helpers
# ---------------------------

def _safe_duration_seconds(path: Path) -> Optional[float]:
    """Try to read duration via mutagen. Return None if not available."""
    try:
        from mutagen import File as MutagenFile
        m = MutagenFile(path)
        if m is not None and getattr(m, "info", None) and getattr(m.info, "length", None):
            return float(m.info.length)
    except Exception:
        pass
    return None


def _build_expected_map(conn) -> Dict[str, List[dict]]:
    """
    Map normalized base name 'artist - title' -> list of track rows.
    Track rows must have: id, artist, name, duration_ms
    """
    rows = conn.execute("""
        SELECT DISTINCT t.id, t.artist, t.name, t.duration_ms
        FROM tracks t
        JOIN playlist_tracks pt ON pt.track_id = t.id
    """).fetchall()
    exp: Dict[str, List[dict]] = {}
    for r in rows:
        base = _norm_base(f"{r['artist']} - {r['name']}")
        exp.setdefault(base, []).append(dict(r))
    return exp


def _is_ignored(rel_path: Path, ignore_dirs: Set[str]) -> bool:
    """Return True if any component of rel_path is in ignore_dirs (case-insensitive)."""
    if not ignore_dirs:
        return False
    ignore_lower = {d.lower() for d in ignore_dirs}
    return any(part.lower() in ignore_lower for part in rel_path.parts)


def _cleanup_missing_files(conn, download_root: Path) -> int:
    """
    Remove database entries for files that no longer exist on disk.
    Returns number of entries removed.
    """
    rows = conn.execute("SELECT track_id, file_path FROM files").fetchall()
    removed = 0
    
    for r in rows:
        file_path = resolve_path(download_root, r["file_path"])
        if not file_path.exists():
            conn.execute("DELETE FROM files WHERE track_id = ?", (r["track_id"],))
            removed += 1
    
    if removed > 0:
        conn.commit()
    
    return removed


def _cleanup_fragment_files(download_root: Path) -> int:
    """
    Remove leftover fragment files from incomplete/failed yt-dlp downloads.
    These are files matching patterns:
    - *.part-Frag*
    - *.part (without extension)
    - *.ytdl
    
    Returns number of files deleted.
    """
    deleted = 0
    
    # Find all fragment files
    for pattern in ["*.part-Frag*", "*.part", "*.ytdl"]:
        for p in download_root.rglob(pattern):
            if p.is_file():
                try:
                    # Extra safety: only delete if it looks like a fragment
                    if "-Frag" in p.name or p.suffix == ".part" or p.suffix == ".ytdl":
                        p.unlink()
                        deleted += 1
                except OSError:
                    pass
    
    return deleted


# ---------------------------
# Public API
# ---------------------------

def rescan_existing_files(download_root: Path, ignore_dirs: Iterable[str] | None = None) -> dict:
    """
    Walk download_root, try to attach files that already exist on disk
    to tracks in the DB, using filename matching and duration as tiebreaker.
    
    Also removes database entries for files that no longer exist on disk.
    Also cleans up leftover fragment files from incomplete downloads.

    ignore_dirs: names of directories to skip anywhere under download_root (case-insensitive),
                 e.g. {"old", ".trash"}.

    Returns stats dict.
    """
    ignore_dirs = set(ignore_dirs or [])
    conn = get_conn()
    
    # First, clean up missing files from the database
    removed = _cleanup_missing_files(conn, download_root)
    
    # Clean up fragment files
    fragments_deleted = _cleanup_fragment_files(download_root)
    
    expected = _build_expected_map(conn)

    scanned = 0
    attached = 0
    ambiguous = 0
    unmatched = 0
    skipped = 0

    # Walk recursively
    for p in download_root.rglob("*"):
        if not p.is_file():
            continue

        rel = p.relative_to(download_root)

        # Skip if any part of the relative path is ignored
        if _is_ignored(rel, ignore_dirs):
            skipped += 1
            continue

        ext = p.suffix.lstrip(".").lower()
        if ext not in AUDIO_EXTS:
            continue

        scanned += 1
        base = _norm_base(p.stem)
        candidates = expected.get(base)

        if not candidates:
            unmatched += 1
            continue

        if len(candidates) == 1:
            attach_file(conn, candidates[0]["id"], rel, download_root)
            attached += 1
            continue

        # Multiple candidates with same base name. Use duration if possible.
        dur = _safe_duration_seconds(p)
        if dur is None:
            ambiguous += 1
            continue

        def delta(c):
            t = (c.get("duration_ms") or 0) / 1000.0
            return abs(t - dur)

        candidates_sorted = sorted(candidates, key=delta)
        best = candidates_sorted[0]
        if delta(best) <= 3.0:
            # If next best is too close, consider ambiguous
            if len(candidates_sorted) > 1 and abs(delta(candidates_sorted[1]) - delta(best)) < 0.5:
                ambiguous += 1
            else:
                attach_file(conn, best["id"], rel, download_root)
                attached += 1
        else:
            ambiguous += 1

    conn.commit()
    return {
        "scanned": scanned,
        "attached": attached,
        "ambiguous": ambiguous,
        "unmatched": unmatched,
        "skipped": skipped,
        "removed": removed,
        "fragments_deleted": fragments_deleted,
    }


def purge_bad_duration_files(download_root: Path, *, max_ratio: float = 0.2, min_abs: float = 15.0) -> dict:
    """
    Remove files whose on-disk duration deviates too much from the track's Spotify
    metadata. Purged tracks will be redownloaded on the next sync.

    Returns stats dict with number of checked and purged files.
    """
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT f.track_id, f.file_path, t.duration_ms
        FROM files f
        JOIN tracks t ON t.id = f.track_id
        """
    ).fetchall()

    checked = 0
    purged = 0
    for r in rows:
        checked += 1
        target = (r["duration_ms"] or 0) / 1000.0 if r["duration_ms"] else None
        if target is None:
            continue
        path = resolve_path(download_root, r["file_path"])
        actual = _safe_duration_seconds(path)
        if actual is None:
            continue
        max_diff = max(min_abs, target * max_ratio)
        if abs(actual - target) > max_diff:
            try:
                path.unlink()
            except OSError:
                pass
            conn.execute("DELETE FROM files WHERE track_id = ?", (r["track_id"],))
            purged += 1
    conn.commit()
    return {"checked": checked, "purged": purged}
