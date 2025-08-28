# downloader.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import shutil
import time
from typing import Optional, Dict, Any, List

from yt_dlp import YoutubeDL

from db import get_conn, get_cached_source, upsert_source, delete_source_cache


# ---------------------------
# Config / helpers
# ---------------------------

REKORDBOX_AUDIO_EXTS = {"mp3", "m4a", "aac", "wav", "aiff", "flac", "alac"}

def _sanitize(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    return re.sub(r"\s+", " ", name).strip()

def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[\[\]\(\)\-\_]+", " ", s)
    s = re.sub(r"[^a-z0-9\s]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _title_score(artist: str, title: str, cand_title: str, cand_uploader: str) -> float:
    t_title = _norm(title)
    t_artist = _norm(artist)
    c_title = _norm(cand_title)
    c_up = _norm(cand_uploader)

    score = 0.0
    for token in t_title.split():
        if token in c_title:
            score += 1.0
    if t_artist in c_title:
        score += 1.5
    if t_artist in c_up:
        score += 1.0
    if "topic" in c_up:
        score += 0.5
    if "official" in c_title:
        score += 0.4
    return score

def _human_bytes(n: Optional[float]) -> str:
    if not n or n <= 0: return "0B"
    for unit in ("B","KB","MB","GB","TB"):
        if n < 1024: return f"{n:,.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"

def _human_time(sec: Optional[float]) -> str:
    if sec is None: return "?:??"
    sec = int(sec)
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def _safe_duration_seconds(path: Path) -> Optional[float]:
    """Return duration in seconds using mutagen; None if unavailable."""
    try:
        from mutagen import File as MutagenFile
        m = MutagenFile(path)
        if m is not None and getattr(m, "info", None) and getattr(m.info, "length", None):
            return float(m.info.length)
    except Exception:
        pass
    return None


@dataclass
class DownloadResult:
    ok: bool
    track_id: Optional[str] = None
    artist: Optional[str] = None
    title: Optional[str] = None
    final_path: Optional[Path] = None
    source_url: Optional[str] = None
    ext: Optional[str] = None
    abr_kbps: Optional[int] = None
    transcoded: bool = False
    error: Optional[str] = None


# ---------------------------
# Fast search + cache
# ---------------------------

def _pick_best(entries: List[Dict[str, Any]], artist: str, title: str, target: Optional[float]) -> Optional[Dict[str, Any]]:
    if not entries:
        return None
    best, best_score = None, -1e9
    for e in entries:
        if not e:
            continue
        dur = e.get("duration")
        # Skip entries wildly off from the expected duration to avoid long DJ sets
        if target is not None and isinstance(dur, (int, float)):
            max_diff = max(15.0, target * 0.2)  # allow ~20% drift or at least 15s
            if abs(dur - target) > max_diff:
                continue

        s = _title_score(artist, title, e.get("title") or "", e.get("uploader") or e.get("channel") or "")
        if target is not None and isinstance(dur, (int, float)):
            diff = abs(dur - target)
            if diff <= 3:
                s += 3.0
            else:
                s -= min(diff / 5.0, 5.0)
        if isinstance(dur, (int, float)) and dur < 60:
            s -= 1.0
        if s > best_score:
            best, best_score = e, s
    return best

def _search_best(artist: str, title: str, duration_ms: Optional[int]) -> Optional[Dict[str, Any]]:
    """
    Fast two-stage search:
      1) quick flat searches on a few query variants (ytsearch6)
      2) widen to ytsearch15 only if needed
    """
    target = (duration_ms or 0) / 1000.0 if duration_ms else None

    qvars = [
        f'ytsearch6:"{artist}" "{title}"',
        f'ytsearch6:{artist} - {title} topic',
        f'ytsearch6:{artist} {title} audio',
    ]
    opts_flat = {
        "quiet": True,
        "noplaylist": True,
        "default_search": "auto",
        "skip_download": True,
        "extract_flat": True,
    }
    with YoutubeDL(opts_flat) as ydl:
        for q in qvars:
            info = ydl.extract_info(q, download=False)
            if not info:
                continue
            entries = info.get("entries") or ([info] if info.get("_type") != "playlist" else [])
            best = _pick_best(entries, artist, title, target)
            if best:
                return best

    # Fallback: slower, wider search
    opts_full = {
        "quiet": True,
        "noplaylist": True,
        "default_search": "auto",
        "skip_download": True,
    }
    with YoutubeDL(opts_full) as ydl:
        info = ydl.extract_info(f"ytsearch15:{artist} - {title}", download=False)
        if not info:
            return None
        entries = info.get("entries") or ([info] if info.get("_type") != "playlist" else [])
        return _pick_best(entries, artist, title, target)


# ---------------------------
# Download core (+ progress)
# ---------------------------

def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None

def _ffmpeg_transcode_to_m4a(src: Path, dst: Path, aac_kbps: int = 192) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-nostdin",
        "-i", str(src),
        "-vn",
        "-c:a", "aac",
        "-b:a", f"{aac_kbps}k",
        str(dst),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def _make_progress_hook(label: str):
    def hook(d: Dict[str, Any]):
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes", 0)
            pct = (done / total * 100.0) if total else 0.0
            spd = d.get("speed")
            eta = d.get("eta")
            line = f"\r  ↳ {label}  [{pct:5.1f}%]  {_human_bytes(spd)}/s  ETA { _human_time(eta) }"
            print(line, end="", flush=True)
        elif status == "finished":
            print(f"\r  ↳ {label}  [100.0%]  Processing…            ", flush=True)
    return hook

def _classify_error(msg: str) -> str:
    m = (msg or "").lower()
    if "copyright" in m or "drm" in m:
        return "copyright/DRM restricted"
    if "geo" in m or "unavailable in your country" in m:
        return "geo-restricted"
    if "age" in m:
        return "age-restricted"
    if "private video" in m or "removed" in m or "not available" in m:
        return "unavailable"
    return msg or "unknown error"


def download_track(
    *,
    track_id: str,
    artist: str,
    title: str,
    duration_ms: Optional[int],
    out_root: Path,
    aac_kbps: int = 192,
    retries: int = 2,
    progress: bool = True
) -> DownloadResult:
    """
    Strategy:
      1) Use cached source URL if available; otherwise fast search (flat).
      2) Prefer native AAC/M4A from YouTube. If not Rekordbox-compatible, transcode once to M4A.
      3) If cached URL fails, clear cache and fall back to fresh search.
    """
    if not _have_ffmpeg():
        return DownloadResult(ok=False, track_id=track_id, artist=artist, title=title, error="ffmpeg not found on PATH")

    label = f"{artist} - {title}"
    try:
        out_root.mkdir(parents=True, exist_ok=True)
        base = _sanitize(label)
        conn = get_conn()

        # Short-circuit if a compatible file already exists and duration matches
        target_sec = (duration_ms or 0) / 1000.0 if duration_ms else None
        for ext in ("m4a", "mp3", "flac", "wav", "aiff", "alac", "aac", "webm", "opus"):
            p = (out_root / base).with_suffix(f".{ext}")
            if p.exists():
                if target_sec is not None:
                    actual = _safe_duration_seconds(p)
                    if actual is not None:
                        max_diff = max(15.0, target_sec * 0.2)
                        if abs(actual - target_sec) > max_diff:
                            if progress:
                                print(f"• {label}\n  ! {p.name} duration mismatch -> redownloading")
                            try:
                                p.unlink()
                            except OSError:
                                pass
                            delete_source_cache(conn, track_id)
                            break
                else:
                    pass
                if p.exists():
                    if progress:
                        print(f"• {label}\n  ✓ {label}  → {p.name}  (exists)")
                    return DownloadResult(ok=True, track_id=track_id, artist=artist, title=title, final_path=p, source_url=None, ext=ext, abr_kbps=None, transcoded=False)

        hooks = [_make_progress_hook(label)] if progress else []

        # 0) Try cache
        cached = get_cached_source(conn, track_id)
        url = None
        if cached:
            url = cached["url"]
            if progress:
                print(f"• {label}  (cached source)")
        else:
            if progress:
                print(f"• {label}  (searching…)")

        def _do_download(u: str) -> DownloadResult:
            ydl_opts = {
                "quiet": True,
                "noplaylist": True,
                "continuedl": True,
                "concurrent_fragment_downloads": 3,
                "retries": 3,
                "fragment_retries": 5,
                "ignoreerrors": False,
                "overwrites": False,
                "noprogress": True,
                "format": "bestaudio[ext=m4a]/bestaudio[acodec^=aac]/bestaudio/best",
                "outtmpl": str((out_root / base).with_suffix(".%(ext)s")),
                "postprocessors": [],
                "progress_hooks": hooks,
            }
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(u, download=True)
                if info.get("_type") == "playlist" and info.get("entries"):
                    info = info["entries"][0]

                ext = (info.get("ext") or "").lower()
                final = None
                candidates = [
                    (out_root / f"{base}.{ext}") if ext else None,
                    (out_root / f"{base}.m4a"),
                    (out_root / f"{base}.mp4"),
                    (out_root / f"{base}.webm"),
                    (out_root / f"{base}.opus"),
                    (out_root / f"{base}.mp3"),
                    (out_root / f"{base}.aac"),
                ]
                for _ in range(5):
                    for p in candidates:
                        if p and p.exists():
                            final = p
                            break
                    if final:
                        break
                    time.sleep(0.5)
                if not final:
                    return DownloadResult(ok=False, track_id=track_id, artist=artist, title=title,
                                          source_url=u,
                                          error="Download reported success but file not found")

                ext = final.suffix.lstrip(".").lower()
                if ext in REKORDBOX_AUDIO_EXTS:
                    abr = int(info["abr"]) if isinstance(info.get("abr"), (int, float)) else None
                    if progress:
                        print(f"  ✓ {label}  → {final.name}")
                    return DownloadResult(ok=True, track_id=track_id, artist=artist, title=title,
                                          final_path=final,
                                          source_url=u, ext=ext, abr_kbps=abr, transcoded=False)

                m4a_path = (out_root / base).with_suffix(".m4a")
                _ffmpeg_transcode_to_m4a(final, m4a_path, aac_kbps=aac_kbps)
                if progress:
                    print(f"  ✓ {label}  → {m4a_path.name}  (transcoded)")
                return DownloadResult(ok=True, track_id=track_id, artist=artist, title=title,
                                      final_path=m4a_path,
                                      source_url=u, ext="m4a", abr_kbps=aac_kbps, transcoded=True)

        # Try cached first; if it fails, drop cache and fall back to search
        if url:
            try:
                res = _do_download(url)
                if res.ok:
                    upsert_source(conn, track_id, url)
                    return res
                else:
                    delete_source_cache(conn, track_id)
            except Exception:
                delete_source_cache(conn, track_id)
                # fall through to fresh search

        # Fresh search
        chosen = _search_best(artist, title, duration_ms)
        if not chosen:
            print(f"  ✗ {label}  (no suitable source found)")
            return DownloadResult(ok=False, track_id=track_id, artist=artist, title=title, error="No suitable YouTube match found")

        url = chosen.get("webpage_url") or chosen.get("url")
        if not url:
            print(f"  ✗ {label}  (missing URL)")
            return DownloadResult(ok=False, track_id=track_id, artist=artist, title=title, error="Selected entry has no URL")

        # Persist cache for next time
        try:
            upsert_source(
                conn, track_id, url,
                title=chosen.get("title"),
                uploader=chosen.get("uploader") or chosen.get("channel"),
                duration_sec=int(chosen["duration"]) if isinstance(chosen.get("duration"), (int, float)) else None,
            )
            conn.commit()
        except Exception:
            pass

        return _do_download(url)

    except Exception as e:
        return DownloadResult(ok=False, track_id=track_id, artist=artist, title=title, error=_classify_error(str(e)))


# ---------------------------
# Batch helper
# ---------------------------

def download_missing_batch(
    rows: List[Dict[str, Any]],
    out_root: Path,
    *,
    aac_kbps: int = 192,
    progress: bool = True
) -> List[DownloadResult]:
    results: List[DownloadResult] = []
    total = len(rows)
    for idx, r in enumerate(rows, 1):
        if progress:
            print(f"\n[{idx}/{total}]")
        res = download_track(
            track_id=r["id"],
            artist=r["artist"],
            title=r["name"],
            duration_ms=r["duration_ms"],
            out_root=out_root,
            aac_kbps=aac_kbps,
            progress=progress,
        )
        results.append(res)
    return results
