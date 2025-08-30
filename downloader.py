# downloader.py
import os
import re
import tempfile
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from db import get_cached_source, upsert_source

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
        "quiet": False,
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
            fn = ydl.prepare_filename(info)
            return True, Path(fn), None
        except Exception as e:
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

    def try_one_url(u: str) -> DownloadResult:
        provider_guess = _infer_provider_from_url(u)
        tag = "[SC]" if provider_guess == "soundcloud" else "[YT]" if provider_guess == "youtube" else "[SRC]"
        pr = _probe_formats(u, allow_missing_pot=False, progress=progress)
        if not pr.ok:
            pr = _probe_formats(u, allow_missing_pot=True, progress=progress)
            if not pr.ok:
                return DownloadResult(False, track_id, artist, title, source_url=u, error=pr.error)

        provider = pr.provider or provider_guess
        plan = _choose_plan(pr.info, provider, progress=progress)  # type: ignore[arg-type]
        if not plan:
            if progress:
                print(f"  {tag} → No healthy formats; forcing 'bestaudio/best'")
            ok, final, err = _targeted_download(u, None, base, out_root, None, aac_kbps, True, progress)
            return DownloadResult(ok, track_id, artist, title, final, u, None, None, False, err)

        fmt_id, mode, note, keep_native = plan
        if progress:
            print(f"  {tag} → Using {fmt_id} ({note})")
        ok, final, err = _targeted_download(u, fmt_id, base, out_root, mode, aac_kbps, keep_native, progress)
        return DownloadResult(ok, track_id, artist, title, final, u, final.suffix.lstrip(".") if final else None,
                              None, not keep_native, err)

    # Candidate ordering
    cached = get_cached_source(get_conn(), track_id)
    cached_url = cached["url"] if cached else None
    cached_provider = _infer_provider_from_url(cached_url) if cached_url else None

    candidates: List[str] = []

    # Cached SC first
    if cached_url and cached_provider == "soundcloud":
        candidates.append(cached_url)

    # Always SC search before YT
    alt_urls = _search_candidates(artist, title, duration_ms, progress=progress)
    sc_alts = [u for u in alt_urls if "soundcloud.com" in u.lower()]
    yt_alts = [u for u in alt_urls if "youtu" in u.lower()]
    candidates.extend(sc_alts)

    # Cached YT deferred
    if cached_url and cached_provider == "youtube":
        candidates.append(cached_url)

    # Then YT search
    candidates.extend(yt_alts)

    if progress:
        print(f"  ⇒ SC-first mode: trying {len(candidates)} candidate(s)")

    for u in candidates:
        res = try_one_url(u)
        if res.ok:
            try:
                upsert_source(get_conn(), track_id, res.source_url or u)
            except Exception:
                pass
            return res

    return DownloadResult(False, track_id, artist, title, error="no usable formats across SC+YT")
