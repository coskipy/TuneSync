# tagger.py
from __future__ import annotations
from pathlib import Path
from typing import Iterable, Optional, Tuple
import requests

from mutagen import MutagenError
from mutagen.mp4 import MP4, MP4Cover
from mutagen.id3 import ID3, TIT2, TALB, TPE1, TPE2, TRCK, TCON, APIC, TDRC
from mutagen.flac import FLAC, Picture

from db import get_conn, resolve_path
from spotify_client import SpotifyClient


def _fetch_cover(url: Optional[str]) -> Tuple[Optional[bytes], Optional[str]]:
    if not url:
        return None, None
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.content, r.headers.get("Content-Type", "")
    except Exception:
        return None, None


def _guess_is_jpeg(mime: Optional[str], sniff: Optional[bytes]) -> bool:
    if mime:
        m = mime.lower()
        if "jpeg" in m or "jpg" in m:
            return True
        if "png" in m:
            return False
    # Sniff a couple of magic bytes if needed
    if sniff:
        # JPEG starts with FF D8 FF
        return sniff[:3] == b"\xff\xd8\xff"
    return True  # default to jpeg; most Spotify images are jpeg


def _year_from_release_date(s: Optional[str]) -> Optional[str]:
    # Spotify gives "YYYY", "YYYY-MM-DD", or None
    if not s:
        return None
    return s[:4]


def _tag_m4a(path: Path, meta: dict, cover_bytes: Optional[bytes], mime: Optional[str]) -> None:
    audio = MP4(path)
    audio["\xa9nam"] = meta["title"]             # Title
    audio["\xa9ART"] = meta["artist"]            # Artist
    audio["aART"]   = meta["album_artist"]       # Album Artist
    audio["\xa9alb"] = meta["album"]             # Album
    if meta.get("track_number"):
        audio["trkn"] = [(int(meta["track_number"]), 0)]
    if meta.get("genres"):
        audio["\xa9gen"] = ", ".join(meta["genres"])
    yr = _year_from_release_date(meta.get("release_date"))
    if yr:
        audio["\xa9day"] = yr

    if cover_bytes:
        is_jpeg = _guess_is_jpeg(mime, cover_bytes)
        fmt = MP4Cover.FORMAT_JPEG if is_jpeg else MP4Cover.FORMAT_PNG
        audio["covr"] = [MP4Cover(cover_bytes, imageformat=fmt)]
    audio.save()


def _tag_mp3(path: Path, meta: dict, cover_bytes: Optional[bytes], mime: Optional[str]) -> None:
    try:
        tags = ID3(path)
    except Exception:
        tags = ID3()

    # Clear previous frames we overwrite (prevents duplicates on repeated runs)
    for fid in ("TIT2", "TPE1", "TPE2", "TALB", "TRCK", "TCON", "APIC", "TDRC"):
        try:
            tags.delall(fid)
        except Exception:
            pass

    tags.add(TIT2(encoding=3, text=meta["title"]))
    tags.add(TPE1(encoding=3, text=meta["artist"]))
    tags.add(TPE2(encoding=3, text=meta["album_artist"]))
    tags.add(TALB(encoding=3, text=meta["album"]))
    if meta.get("track_number"):
        tags.add(TRCK(encoding=3, text=str(meta["track_number"])))
    if meta.get("genres"):
        tags.add(TCON(encoding=3, text=", ".join(meta["genres"])))
    yr = _year_from_release_date(meta.get("release_date"))
    if yr:
        tags.add(TDRC(encoding=3, text=yr))
    if cover_bytes:
        mime_use = "image/jpeg" if _guess_is_jpeg(mime, cover_bytes) else "image/png"
        tags.add(APIC(encoding=3, mime=mime_use, type=3, desc="Cover", data=cover_bytes))

    tags.save(path)


def _tag_flac(path: Path, meta: dict, cover_bytes: Optional[bytes], mime: Optional[str]) -> None:
    audio = FLAC(path)
    audio["title"] = meta["title"]
    audio["artist"] = meta["artist"]
    audio["albumartist"] = meta["album_artist"]
    audio["album"] = meta["album"]
    if meta.get("track_number"):
        audio["tracknumber"] = str(meta["track_number"])
    if meta.get("genres"):
        audio["genre"] = ", ".join(meta["genres"])
    yr = _year_from_release_date(meta.get("release_date"))
    if yr:
        audio["date"] = yr

    # Replace existing pictures
    try:
        audio.clear_pictures()
    except Exception:
        pass

    if cover_bytes:
        pic = Picture()
        pic.type = 3  # front cover
        pic.mime = "image/jpeg" if _guess_is_jpeg(mime, cover_bytes) else "image/png"
        pic.desc = "Cover"
        pic.data = cover_bytes
        audio.add_picture(pic)
    audio.save()


def tag_tracks_in_db(download_root: Path, track_ids: Optional[Iterable[str]] = None) -> dict:
    """
    Tag files referenced in DB with Spotify metadata & cover art.
    If track_ids is None, tag everything that has a file.
    Returns stats dict.
    """
    conn = get_conn()
    if track_ids is None:
        rows = conn.execute("SELECT f.track_id, f.file_path FROM files f").fetchall()
    else:
        qmarks = ",".join("?" for _ in track_ids)
        rows = conn.execute(
            f"SELECT f.track_id, f.file_path FROM files f WHERE f.track_id IN ({qmarks})",
            tuple(track_ids),
        ).fetchall()

    done = 0
    skipped = 0
    errors = 0

    path_map = {}
    for r in rows:
        tid = r["track_id"]
        abs_path = resolve_path(download_root, r["file_path"])
        if not abs_path.exists():
            skipped += 1
            continue
        path_map[tid] = abs_path

    if path_map:
        try:
            metas = SpotifyClient.get_tracks_details(list(path_map.keys()))
        except Exception:
            errors += len(path_map)
            return {"tagged": done, "skipped": skipped, "errors": errors}

        meta_ids = set()
        for full in metas:
            tid = full["id"]
            meta_ids.add(tid)
            abs_path = path_map.get(tid)
            if not abs_path:
                continue

            try:
                cover_bytes, mime = _fetch_cover(full.get("cover_url"))

                meta = {
                    "title":        full["title"],
                    "artist":       full["artist"],
                    "album_artist": full["album_artist"],
                    "album":        full["album"],
                    "track_number": full.get("track_number"),
                    "genres":       full.get("genres") or [],
                    "release_date": full.get("release_date"),
                }

                ext = abs_path.suffix.lower()
                if ext == ".m4a" or ext == ".mp4":
                    _tag_m4a(abs_path, meta, cover_bytes, mime)
                elif ext == ".mp3":
                    _tag_mp3(abs_path, meta, cover_bytes, mime)
                elif ext == ".flac":
                    _tag_flac(abs_path, meta, cover_bytes, mime)
                else:
                    # leave WAV/AIFF/etc untagged
                    skipped += 1
                    continue

                done += 1
            except MutagenError:
                errors += 1
            except Exception:
                # If a network hiccup or Spotify outage happens mid-run, just count error and continue
                errors += 1

        missing = set(path_map.keys()) - meta_ids
        errors += len(missing)

    return {"tagged": done, "skipped": skipped, "errors": errors}
