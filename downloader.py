# downloader.py
import os
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any

from yt_dlp import YoutubeDL

from db import get_conn, get_cached_source, upsert_source, attach_file
from tagger import tag_tracks_in_db

# Environment
USE_COOKIES = os.getenv("LS_COOKIES", "0") == "1"
COOKIES_BROWSER = os.getenv("LS_COOKIES_BROWSER", "firefox")

YT_EXTRACTOR_ARGS = {
    "youtube": {
        "player_client": ["web", "ios", "web_embedded"]
    }
}

# Minimum bitrate threshold
MIN_ABR = 128


# -------------------------------
# Helpers
# -------------------------------

def _infer_provider_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    u = url.lower()
    if "soundcloud.com" in u:
        return "soundcloud"
    if "youtu" in u:
        return "youtube"
    return None


def _search_candidates(artist: str, title: str, duration_ms: Optional[int], progress: bool = False) -> List[str]:
    """Search for candidates on SoundCloud first, then YouTube."""
    queries = [
        f'scsearch10:"{artist}" "{title}"',
        f'ytsearch10:{artist} {title}'
    ]
    urls = []
    with YoutubeDL({"quiet": True}) as ydl:
        for q in queries:
            try:
                info = ydl.extract_info(q, download=False)
                if "entries" in info:
                    for e in info["entries"]:
                        if e and e.get("url"):
                            urls.append(e["url"])
            except Exception:
                continue
    return urls


def _probe_formats(url: str, allow_missing_pot: bool = False, progress: bool = False):
    opts = {
        "quiet": True,
        "extract_flat": False,
        "skip_download": True,
        "forcejson": True,
        "noplaylist": True,
        "extractor_args": YT_EXTRACTOR_ARGS.copy(),
    }
    if USE_COOKIES:
        opts["cookiesfrombrowser"] = (COOKIES_BROWSER,)
    if allow_missing_pot:
        opts["extractor_args"]["youtube"]["formats"] = ["missing_pot"]

    with YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            return type("Probe", (), {
                "ok": True,
                "info": info.get("formats", []),
                "provider": _infer_provider_from_url(url),
                "error": None
            })
        except Exception as e:
            return type("Probe", (), {"ok": False, "error": str(e)})


def _targeted_download(url: str, fmt_id: Optional[str], base: str, out_root: Path,
                       mode: Optional[str], aac_kbps: int, keep_native: bool, progress: bool = False):
    """Download a chosen format."""
    outtmpl = str(out_root / f"{base}.%(ext)s")
    opts = {
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 3,
        "continuedl": True,
        "fragment_retries": 5,
        "ignoreerrors": False,
        "format": fmt_id or "bestaudio/best",
        "extractor_args": YT_EXTRACTOR_ARGS.copy(),
    }
    if USE_COOKIES:
        opts["cookiesfrombrowser"] = (COOKIES_BROWSER,)

    with YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
            fn = Path(ydl.prepare_filename(info))
            if not fn.exists():
                return False, None, "download reported success but file missing"
            return True, fn, None
        except Exception as e:
            print(f"yt-dlp error: {e}", file=sys.stderr)
            return False, None, str(e)


def _choose_plan(formats: List[Dict[str, Any]], provider: str, progress: bool = False):
    """Select the best format based on provider and quality."""
    best, best_score = None, -1
    for f in formats:
        abr = f.get("abr")
        if abr and abr < MIN_ABR:
            continue

        score = 0
        if f.get("vcodec") == "none":
            score += 5

        if provider == "soundcloud":
            score += 100  # SC priority bump

        fmt_id = f.get("format_id", "")
        acodec = f.get("acodec")

        if "download" in fmt_id:
            score += 90
        elif "http_mp3" in fmt_id:
            score += 80
        elif "hls_aac" in fmt_id:
            score += 70
        elif "hls_mp3" in fmt_id:
            score += 60
        elif acodec in ("mp4a.40.2", "aac"):
            score += 50
        elif acodec == "opus":
            score += 40
        else:
            score += 10

        if score > best_score:
            best, best_score = (f["format_id"], None, f.get("format_note", ""), True), score

    return best


# -------------------------------
# Public API
# -------------------------------

class DownloadResult:
    def __init__(self, ok: bool, track_id: str, artist: str, title: str,
                 final_path: Optional[Path] = None, source_url: Optional[str] = None,
                 ext: Optional[str] = None, abr_kbps: Optional[int] = None,
                 transcoded: bool = False, error: Optional[str] = None):
        self.ok = ok
        self.track_id = track_id
        self.artist = artist
        self.title = title
        self.final_path = final_path
        self.source_url = source_url
        self.ext = ext
        self.abr_kbps = abr_kbps
        self.transcoded = transcoded
        self.error = error


def download_track(track_id: str, artist: str, title: str,
                   duration_ms: Optional[int], out_root: Path,
                   aac_kbps: int = 192, progress: bool = False) -> DownloadResult:
    """Download a single track preferring SoundCloud sources."""

    base = f"{artist} - {title}"

    if progress:
        print("  Searching SoundCloud/YouTube for matches…")

    def try_one_url(u: str) -> DownloadResult:
        provider_guess = _infer_provider_from_url(u)
        pr = _probe_formats(u, allow_missing_pot=False, progress=progress)
        if not pr.ok:
            pr = _probe_formats(u, allow_missing_pot=True, progress=progress)
            if not pr.ok:
                return DownloadResult(False, track_id, artist, title, source_url=u, error=pr.error)

        provider = pr.provider or provider_guess or "source"
        plan = _choose_plan(pr.info, provider, progress=progress)  # type: ignore[arg-type]
        if not plan:
            fmt_id, mode, note, keep_native = None, None, "", True
        else:
            fmt_id, mode, note, keep_native = plan

        if progress:
            print(f"  Match found on {provider}")
            if not keep_native:
                print("  Conversion required")
            print("  Downloading…")

        ok, final, err = _targeted_download(u, fmt_id, base, out_root, mode, aac_kbps, keep_native, progress)

        if progress:
            if ok:
                print("  Download complete")
            else:
                print(f"  Download failed: {err}")

        return DownloadResult(ok, track_id, artist, title, final, u, final.suffix.lstrip(".") if final else None,
                              None, not keep_native, err)

    # Candidate ordering: cached SoundCloud → SoundCloud search → cached YouTube → YouTube search
    with get_conn() as conn:
        cached = get_cached_source(conn, track_id)
    cached_url = cached["url"] if cached else None
    cached_provider = _infer_provider_from_url(cached_url) if cached_url else None

    candidates: List[str] = []

    if cached_url and cached_provider == "soundcloud":
        candidates.append(cached_url)

    alt_urls = _search_candidates(artist, title, duration_ms, progress=progress)
    sc_alts = [u for u in alt_urls if "soundcloud.com" in u.lower()]
    yt_alts = [u for u in alt_urls if "youtu" in u.lower()]
    candidates.extend(sc_alts)

    if cached_url and cached_provider == "youtube":
        candidates.append(cached_url)

    candidates.extend(yt_alts)

    for u in candidates:
        res = try_one_url(u)
        if res.ok:
            try:
                with get_conn() as conn:
                    upsert_source(conn, track_id, res.source_url or u)
            except Exception:
                pass
            return res

    return DownloadResult(False, track_id, artist, title, error="no usable formats across SC+YT")


def download_missing_batch(
    rows: List[Dict[str, Any]],
    out_root: Path,
    *,
    aac_kbps: int = 192,
    progress: bool = True,
) -> List[DownloadResult]:
    """Download a batch of tracks, tagging and recording successes."""
    results: List[DownloadResult] = []
    success_ids: List[str] = []
    total = len(rows)
    with get_conn() as conn:
        for idx, r in enumerate(rows, 1):
            if progress:
                print(f"\n[{idx}/{total}] {r['artist']} - {r['name']}")
            res = download_track(
                track_id=r["id"],
                artist=r["artist"],
                title=r["name"],
                duration_ms=r["duration_ms"] if "duration_ms" in r.keys() else None,
                out_root=out_root,
                aac_kbps=aac_kbps,
                progress=progress,
            )
            results.append(res)
            if res.ok and res.final_path:
                try:
                    rel = res.final_path.relative_to(out_root)
                except ValueError:
                    rel = Path(res.final_path.name)
                attach_file(conn, res.track_id, rel, out_root)
                success_ids.append(res.track_id)
    if success_ids:
        tag_tracks_in_db(out_root, success_ids)
    return results
