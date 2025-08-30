from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import shutil
import time
import traceback
from typing import Optional, Dict, Any, List, Tuple

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError  # correct import location

from db import get_conn, get_cached_source, upsert_source, delete_source_cache


# ===========================
# Config / helpers
# ===========================

REKORDBOX_AUDIO_EXTS = {"mp3", "m4a", "aac", "wav", "aiff", "flac", "alac", "opus", "webm"}

# Limit how many alternative uploads we try per failed track
ALT_SEARCH_TRIES = 6

# How strict to be on duration matching (seconds)
# (loosened a bit to prevent false negatives)
DUR_ABS_SLACK = 15        # absolute ±15s
DUR_REL_SLACK = 0.20      # or ±20%

# Minimum acceptable quality (approx.) for keep-native
MIN_ABR_KBPS = 128

# Prefer SoundCloud over YouTube when both are viable
PROVIDER_BONUS = {"soundcloud": 100, "youtube": 0, "other": 0}

# Preferred YouTube player clients (env override)
_YT_CLIENTS = os.getenv("LS_YT_CLIENTS", "web,ios,web_embedded").strip()
YOUTUBE_CLIENTS = [c.strip() for c in _YT_CLIENTS.split(",") if c.strip()] or ["web"]

# Geo-bypass hint (optional)
GEO_BYPASS_COUNTRY = os.getenv("LS_GEO_COUNTRY", "").strip() or None

# Cookies (env-driven)
USE_COOKIES = os.getenv("LS_COOKIES", "0") not in ("0", "", "false", "False", "no", "NO")
COOKIES_BROWSER = os.getenv("LS_COOKIES_BROWSER", "").strip() or None


def _sanitize(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    return re.sub(r"\s+", " ", name).strip()


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


def _human_bytes(n: Optional[float]) -> str:
    if not n or n <= 0:
        return "0B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:,.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def _human_time(sec: Optional[float]) -> str:
    if sec is None:
        return "?:??"
    sec = int(sec)
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


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


def _classify_error(msg: str) -> str:
    m = (msg or "").lower()
    if "po token" in m or "missing_pot" in m:
        return "youtube PO token required for healthy audio"
    if "copyright" in m or "drm" in m:
        return "copyright/DRM restricted"
    if "geo" in m or "unavailable in your country" in m:
        return "geo-restricted"
    if "age" in m:
        return "age-restricted"
    if "private video" in m or "removed" in m or "not available" in m:
        return "unavailable"
    if "requested format is not available" in m:
        return "requested format not available"
    if "http error 403" in m or " 403" in m:
        return "http 403 (likely protected client)"
    return msg or "unknown error"


def _infer_provider_from_url(u: str) -> str:
    s = u.lower()
    if "soundcloud.com" in s:
        return "soundcloud"
    if "youtube.com" in s or "youtu.be" in s:
        return "youtube"
    return "other"


# ===========================
# Probe, choose, download
# ===========================

@dataclass
class _ProbeResult:
    ok: bool
    info: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    provider: str = "other"


def _base_ydl_opts() -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "noplaylist": True,
        "nocheckcertificate": True,
        "quiet": True,
        "extractor_args": {"youtube": {"player_client": YOUTUBE_CLIENTS}},
    }
    if USE_COOKIES and COOKIES_BROWSER:
        opts["cookiesfrombrowser"] = (COOKIES_BROWSER,)
    if GEO_BYPASS_COUNTRY:
        opts["geo_bypass_country"] = GEO_BYPASS_COUNTRY
    return opts


def _probe_formats(url: str, *, allow_missing_pot: bool, progress: bool) -> _ProbeResult:
    """Extract formats without selection or test-downloads. Optionally expose formats that need PO tokens."""
    opts = _base_ydl_opts()
    if allow_missing_pot:
        # Reveal formats that usually require PO tokens (may 403 on test/download)
        ex = opts.setdefault("extractor_args", {})
        y = ex.setdefault("youtube", {})
        # Preserve preferred clients but allow exposing missing_pot
        y["formats"] = ["missing_pot"]

    try:
        with YoutubeDL(opts) as y:
            info = y.extract_info(url, download=False)
            if info.get("_type") == "playlist" and info.get("entries"):
                info = info["entries"][0]
            prov = "soundcloud" if "soundcloud" in (info.get("extractor") or "").lower() else \
                   "youtube" if "youtube" in (info.get("extractor") or "").lower() else \
                   _infer_provider_from_url(url)
            if progress:
                formats = info.get("formats") or []
                print(f"  → Probed {len(formats)} formats")
                for f in formats[:20]:
                    print(
                        f"     id={f.get('format_id')}, ext={f.get('ext')}, "
                        f"acodec={f.get('acodec')}, vcodec={f.get('vcodec')}, "
                        f"abr={f.get('abr')}, tbr={f.get('tbr')}, proto={f.get('protocol')}"
                    )
                if len(formats or []) > 20:
                    print("     … truncated …")
            return _ProbeResult(ok=True, info=info, provider=prov)
    except Exception as e:
        if progress:
            print("  ⚠️ Probe failed:")
            traceback.print_exc()
        return _ProbeResult(ok=False, error=_classify_error(str(e)), provider=_infer_provider_from_url(url))


def _is_m3u8(fmt: Dict[str, Any]) -> bool:
    proto = (fmt.get("protocol") or "").lower()
    return proto.startswith("m3u8")


def _format_score(f: Dict[str, Any], provider: str) -> Tuple[int, str, bool]:
    """
    Return (score, note, keep_native_flag).
    keep_native_flag=True => format is Rekordbox-friendly and >=128 kbps (or unknown), so don't transcode.
    """
    ext = (f.get("ext") or "").lower()
    vcodec = (f.get("vcodec") or "").lower() if f.get("vcodec") is not None else "none"
    acodec = (f.get("acodec") or "").lower() if f.get("acodec") is not None else "none"
    abr = f.get("abr")
    tbr = f.get("tbr")
    fmt_id = f.get("format_id") or ""
    audio_only = vcodec in (None, "none")

    # Effective audio bitrate hint
    eff_abr = None
    if isinstance(abr, (int, float)):
        eff_abr = int(abr)
    elif isinstance(tbr, (int, float)):
        eff_abr = int(tbr)

    # Minimum quality gate (soft): we still score low-bitrate, but below min gets big penalty
    quality_ok = (eff_abr is None) or (eff_abr >= MIN_ABR_KBPS)

    # Base provider bias
    score = PROVIDER_BONUS.get(provider, PROVIDER_BONUS["other"])

    # Prefer audio-only
    if audio_only:
        score += 12

    # Penalize m3u8 a bit (more brittle than direct HTTP)
    if _is_m3u8(f):
        score -= 6

    note_bits = []

    # ----- Provider-specific preferences -----
    if provider == "soundcloud":
        # 1) True original download (wav/flac/mp3/etc)
        if "download" in fmt_id or (f.get("format_note") or "").lower() == "original":
            score += 90
            note_bits.append("SC original")
        # 2) Direct MP3 over HTTP (stable, usually 128/320)
        elif "http_mp3" in fmt_id:
            score += 82
            note_bits.append("SC http mp3")
        # 3) AAC @ 160 (HLS) is solid
        elif "hls_aac" in fmt_id:
            score += 74
            note_bits.append("SC hls aac")
        # 4) HLS MP3 128
        elif "hls_mp3" in fmt_id:
            score += 66
            note_bits.append("SC hls mp3")
        # 5) HLS Opus 64 (last resort)
        elif "hls_opus" in fmt_id or acodec == "opus":
            score += 15
            note_bits.append("SC opus 64")
        else:
            # Unknown; small nudge
            score += 8
            note_bits.append("SC other")
    else:
        # YouTube and everything else
        if audio_only and acodec in ("mp4a.40.2", "mp4a.40.5", "aac"):
            score += 68
            note_bits.append("YT aac audio-only")
        elif audio_only and acodec == "opus":
            score += 60
            note_bits.append("YT opus audio-only")
        elif acodec not in (None, "none") and vcodec not in (None, "none"):
            score += 35
            note_bits.append("muxed av")
        else:
            score += 10
            note_bits.append("other")

    # Quality penalty/bonus
    if eff_abr is not None:
        # reward higher bitrates a bit
        score += min(10, max(-10, (eff_abr - MIN_ABR_KBPS) // 32))
        if eff_abr < MIN_ABR_KBPS:
            score -= 25
            note_bits.append(f"{eff_abr}kbps(<{MIN_ABR_KBPS})")
        else:
            note_bits.append(f"{eff_abr}kbps")
    else:
        note_bits.append("abr?")

    # Keep-native decision: Rekordbox-friendly container + meets (or unknown) bitrate
    keep_native = (ext in REKORDBOX_AUDIO_EXTS) and quality_ok

    # Human note
    note = "; ".join(note_bits) or ext or fmt_id
    return int(score), note, keep_native


def _choose_plan(info: Dict[str, Any], provider: str, *, progress: bool) -> Optional[Tuple[str, str, str, bool]]:
    """
    Decide the best single format via scoring.
    Returns (format_id, mode, note, keep_native) or None if no usable audio at all.

    mode is for logging: {"native_ok", "transcode", "video_fallback", "hls_audio"} (best-effort labels)
    """
    formats = info.get("formats") or []
    if not formats:
        return None

    best_f = None
    best_score = -10**9
    best_note = ""
    best_keep = False

    for f in formats:
        # Skip non-audio entirely?
        ac = (f.get("acodec") or "").lower()
        if ac in ("", "none", None):
            # If it's muxed (has video and audio), yt-dlp marks acodec!=none; so skip pure video
            continue

        score, note, keep_native = _format_score(f, provider)
        if score > best_score:
            best_score, best_f, best_note, best_keep = score, f, note, keep_native

    if not best_f:
        return None

    fmt_id = best_f.get("format_id")
    ext = (best_f.get("ext") or "").lower()
    vcodec = (best_f.get("vcodec") or "").lower() if best_f.get("vcodec") is not None else "none"

    # Mode label (best-effort)
    if best_keep:
        mode = "native_ok"
    elif vcodec in (None, "none") and _is_m3u8(best_f):
        mode = "hls_audio"
    elif vcodec not in (None, "none"):
        mode = "video_fallback"
    else:
        mode = "transcode"

    # Progress print
    if progress:
        approx = ""
        abr = best_f.get("abr") or best_f.get("tbr")
        if isinstance(abr, (int, float)):
            approx = f"; ~{int(abr)} kbps"
        print(f"  → Chosen format: {fmt_id}  [{mode}; {ext} {best_note}{approx}]")

    return fmt_id, mode, best_note, best_keep


def _targeted_download(
    url: str,
    fmt_id: Optional[str],
    *,
    base: str,
    out_root: Path,
    mode: Optional[str],
    aac_kbps: int,
    keep_native: bool,
    progress: bool
) -> Tuple[bool, Optional[Path], Optional[str]]:
    """
    Download exactly fmt_id (if provided). If fmt_id is None, try 'bestaudio/best'.
    Returns (ok, final_path, error_str)

    keep_native=True => do NOT transcode if the file ends up Rekordbox-friendly (and quality >= MIN_ABR_KBPS)
    """
    outtmpl = str((out_root / base).with_suffix(".%(ext)s"))
    ydl_opts: Dict[str, Any] = {
        "verbose": True,
        "noplaylist": True,
        "outtmpl": outtmpl,
        "retries": 3,
        "fragment_retries": 5,
        "continuedl": True,
        "ignoreerrors": False,
        "http_headers": {
            # Use a "webby" UA by default (works better with web client)
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.5",
        },
        "extractor_args": {"youtube": {"player_client": YOUTUBE_CLIENTS}},
        "quiet": False,  # show progress lines on failures
    }
    if USE_COOKIES and COOKIES_BROWSER:
        ydl_opts["cookiesfrombrowser"] = (COOKIES_BROWSER,)
    if GEO_BYPASS_COUNTRY:
        ydl_opts["geo_bypass_country"] = GEO_BYPASS_COUNTRY

    if fmt_id:
        ydl_opts["format"] = fmt_id
    else:
        ydl_opts["format"] = "bestaudio/best"

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info2 = ydl.extract_info(url, download=True)
            if info2.get("_type") == "playlist" and info2.get("entries"):
                info2 = info2["entries"][0]

            ext = (info2.get("ext") or "").lower()
            final: Optional[Path] = None
            candidates = [
                (out_root / f"{base}.{ext}") if ext else None,
                (out_root / f"{base}.m4a"),
                (out_root / f"{base}.mp4"),
                (out_root / f"{base}.webm"),
                (out_root / f"{base}.opus"),
                (out_root / f"{base}.aac"),
                (out_root / f"{base}.mp3"),
                (out_root / f"{base}.wav"),
                (out_root / f"{base}.flac"),
            ]
            # give fs a moment
            for _ in range(6):
                for p in candidates:
                    if p and p.exists():
                        final = p
                        break
                if final:
                    break
                time.sleep(0.4)

            if not final:
                return False, None, "download reported success but file not found"

            # Decide whether to transcode
            if keep_native and final.suffix.lower().lstrip(".") in REKORDBOX_AUDIO_EXTS:
                if progress:
                    print(f"  ✓ → {final.name} (kept native)")
                return True, final, None

            # If not keep_native, only transcode if NOT Rekordbox-friendly
            if final.suffix.lower().lstrip(".") not in REKORDBOX_AUDIO_EXTS:
                m4a_path = final.with_suffix(".m4a")
                try:
                    _ffmpeg_transcode_to_m4a(final, m4a_path, aac_kbps=aac_kbps)
                    try:
                        if m4a_path.resolve() != final.resolve():
                            final.unlink(missing_ok=True)  # type: ignore[arg-type]
                    except Exception:
                        pass
                    tag = {
                        "opus_transcode": "transcoded from Opus/WebM",
                        "video_fallback": "extracted from video",
                        "hls_audio": "HLS → m4a",
                        None: "transcoded",
                    }.get(mode, "transcoded")
                    if progress:
                        print(f"  ✓ → {m4a_path.name} ({tag})")
                    return True, m4a_path, None
                except Exception as ff:
                    return False, None, f"ffmpeg transcode failed: {ff}"

            # Otherwise keep as-is
            if progress:
                print(f"  ✓ → {final.name} (native container)")
            return True, final, None

    except (DownloadError, ExtractorError) as e:
        if progress:
            print("\n  ⚠️ yt-dlp failed during download:")
            traceback.print_exc()
        return False, None, _classify_error(str(e))
    except Exception as e:
        if progress:
            print("\n  ⚠️ Unexpected downloader error:")
            traceback.print_exc()
        return False, None, _classify_error(str(e))


# ===========================
# Search helpers (SC-first, smart alt uploads)
# ===========================

_BAD_TITLE_WORDS = [
    "remix", "mashup", "bootleg", "cover", "live", "sped up", "speed up",
    "slowed", "reverb", "nightcore", "8d audio", "edit", "extended", "slowed+reverb",
]

def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[\[\]\(\)\-\_]+", " ", s)
    s = re.sub(r"[^a-z0-9\s]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _keywords_in(s: str, words: List[str]) -> bool:
    s2 = _norm(s)
    return any(w in s2 for w in words)

def _looks_like_same_track(sp_artist: str, sp_title: str, cand_title: str, cand_uploader: str) -> float:
    """Score candidate textual similarity without overfitting."""
    want_t = _norm(sp_title)
    want_a = _norm(sp_artist)
    ct = _norm(cand_title)
    cu = _norm(cand_uploader or "")
    score = 0.0
    for tok in want_t.split():
        if tok and tok in ct:
            score += 1.0
    if want_a in ct:
        score += 1.5
    if want_a in cu:
        score += 0.8
    if "topic" in cu:
        score += 0.5
    if "official" in ct:
        score += 0.3
    return score

def _duration_okay(sp_ms: Optional[int], cand_sec: Optional[float]) -> bool:
    if not sp_ms or cand_sec is None:
        return True
    target = sp_ms / 1000.0
    slack = max(DUR_ABS_SLACK, target * DUR_REL_SLACK)
    return abs(cand_sec - target) <= slack

def _collect_search_entries(query: str) -> List[Dict[str, Any]]:
    """Run a search query with common options and return entries (may include both YT and SC style dicts)."""
    opts = _base_ydl_opts()
    opts.update({
        "quiet": True,
        "skip_download": True,
        "default_search": "auto",
    })
    with YoutubeDL(opts) as y:
        info = y.extract_info(query, download=False)
        if info is None:
            return []
        if info.get("_type") == "playlist" and info.get("entries"):
            return [e for e in info["entries"] if e]
        return [info]

def _search_candidates(artist: str, title: str, duration_ms: Optional[int], progress: bool) -> List[str]:
    """
    Return up to ALT_SEARCH_TRIES candidate URLs filtered by:
      - duration proximity (±15s or ±20%)
      - avoid remix/mashup/etc unless those words are IN the Spotify title

    Now runs **SoundCloud first**, then YouTube, and merges.
    """
    allow_words = [w for w in _BAD_TITLE_WORDS if w in _norm(title)]
    block_words = [w for w in _BAD_TITLE_WORDS if w not in allow_words]

    # Slightly overfetch to allow filtering
    sc_q = f'scsearch{ALT_SEARCH_TRIES + 8}:"{artist}" "{title}"'
    yt_q = f'ytsearch{ALT_SEARCH_TRIES + 8}:"{artist}" "{title}" audio'

    urls: List[Tuple[float, str]] = []

    def process(entries: List[Dict[str, Any]], provider_hint: str):
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for e in entries:
            if not e:
                continue
            dur = e.get("duration")
            dur_s = float(dur) if isinstance(dur, (int, float)) else None
            if not _duration_okay(duration_ms, dur_s):
                continue

            title_c = e.get("title") or ""
            uploader_c = e.get("uploader") or e.get("channel") or e.get("uploader_id") or ""

            # Block suspicious edits unless allowed by original title
            if _keywords_in(title_c, block_words):
                continue

            sc = _looks_like_same_track(artist, title, title_c, uploader_c)

            # Provider bias: SC first
            sc += (PROVIDER_BONUS.get(provider_hint, 0) / 100.0)

            # Nudge official-ish
            if "topic" in _norm(uploader_c) or "official" in _norm(title_c):
                sc += 0.5

            scored.append((sc, e))

        # Sort by descending score
        scored.sort(key=lambda x: -x[0])
        for sc, e in scored[: ALT_SEARCH_TRIES * 2]:
            u = e.get("webpage_url") or e.get("url")
            if u:
                urls.append((sc, u))

    # Run SC then YT
    try:
        sc_entries = _collect_search_entries(sc_q)
    except Exception:
        sc_entries = []
    process(sc_entries, "soundcloud")

    try:
        yt_entries = _collect_search_entries(yt_q)
    except Exception:
        yt_entries = []
    process(yt_entries, "youtube")

    # Deduplicate, preserve order by score (SC-biased already)
    seen = set()
    ordered: List[str] = []
    for _, u in sorted(urls, key=lambda x: -x[0]):
        if u not in seen:
            seen.add(u)
            ordered.append(u)

    picks = ordered[:ALT_SEARCH_TRIES]

    if progress:
        sc_ct = sum(1 for u in picks if "soundcloud.com" in u.lower())
        yt_ct = sum(1 for u in picks if "youtu" in u.lower())
        print(f"  → Found {len(picks)} filtered alternatives across SoundCloud + YouTube "
              f"(SC:{sc_ct} / YT:{yt_ct})")

    return picks


# ===========================
# Public API
# ===========================

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
    Robust downloader with provider bias:
      1) Probe cached URL → score formats (SC preferred) → targeted download.
      2) On failure, clear cache and search for filtered alternates (SC first), try each in order.
      3) Keep native if Rekordbox-friendly and >=128 kbps (or unknown bitrate).
      4) Only after all attempts fail, return an error with a concise reason.
    """
    if not _have_ffmpeg():
        return DownloadResult(ok=False, track_id=track_id, artist=artist, title=title, error="ffmpeg not found on PATH")

    label = f"{artist} - {title}"
    out_root.mkdir(parents=True, exist_ok=True)
    base = _sanitize(label)
    conn = get_conn()

    # Short-circuit if suitable file already exists (and duration sane)
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
            if p.exists():
                if progress:
                    print(f"• {label}\n  ✓ {label}  → {p.name}  (exists)")
                return DownloadResult(
                    ok=True, track_id=track_id, artist=artist, title=title,
                    final_path=p, source_url=None, ext=ext, abr_kbps=None, transcoded=False
                )

    if progress:
        print(f"• {label}")

    def try_one_url(u: str) -> DownloadResult:
        provider_guess = _infer_provider_from_url(u)
        tag = "[SC]" if provider_guess == "soundcloud" else "[YT]" if provider_guess == "youtube" else "[SRC]"
        # Probe without missing_pot; if YT pain, allow exposure and let scoring decide
        pr = _probe_formats(u, allow_missing_pot=False, progress=progress)
        if not pr.ok:
            pr = _probe_formats(u, allow_missing_pot=True, progress=progress)
            if not pr.ok:
                return DownloadResult(ok=False, track_id=track_id, artist=artist, title=title,
                                      source_url=u, error=pr.error or "probe failed")

        provider = pr.provider or provider_guess
        plan = _choose_plan(pr.info, provider, progress=progress)  # type: ignore[arg-type]
        if not plan:
            if progress:
                print(f"  {tag} → No healthy formats; forcing 'bestaudio/best' and extracting audio")
            ok, final, err = _targeted_download(
                u, None, base=base, out_root=out_root,
                mode=None, aac_kbps=aac_kbps, keep_native=True, progress=progress
            )
            if not ok:
                return DownloadResult(ok=False, track_id=track_id, artist=artist, title=title, source_url=u, error=err)
            return DownloadResult(ok=True, track_id=track_id, artist=artist, title=title,
                                  final_path=final, source_url=u, ext=final.suffix.lstrip("."), abr_kbps=None, transcoded=False)

        fmt_id, mode, note, keep_native = plan
        if progress:
            print(f"  {tag} → Using {fmt_id} ({note})")

        ok, final, err = _targeted_download(
            u, fmt_id, base=base, out_root=out_root,
            mode=mode, aac_kbps=aac_kbps, keep_native=keep_native, progress=progress
        )
        if not ok:
            return DownloadResult(ok=False, track_id=track_id, artist=artist, title=title, source_url=u, error=err)
        return DownloadResult(ok=True, track_id=track_id, artist=artist, title=title,
                              final_path=final, source_url=u, ext=final.suffix.lstrip("."), abr_kbps=None,
                              transcoded=(not keep_native))

    # 0) Try cached source if present (could be SC or YT)
    cached = get_cached_source(conn, track_id)
    url = cached["url"] if cached else None
    if url:
        if progress:
            print("  (cached source)")
        res = try_one_url(url)
        if res.ok:
            try:
                upsert_source(conn, track_id, url)
                conn.commit()
            except Exception:
                pass
            return res
        # If cached URL fails, purge it so we don't loop on a bad link
        try:
            delete_source_cache(conn, track_id)
            conn.commit()
        except Exception:
            pass

    # 1) Try alternative candidates (SC-first, then YT)
    alt_urls = _search_candidates(artist, title, duration_ms, progress=progress)
    for idx, u in enumerate(alt_urls, 1):
        if progress:
            pref = "[SC]" if "soundcloud.com" in u.lower() else "[YT]" if "youtu" in u.lower() else "[SRC]"
            print(f"  → Trying alternative {pref} [{idx}/{len(alt_urls)}]: {u}")
        res = try_one_url(u)
        if res.ok:
            try:
                upsert_source(conn, track_id, u)
                conn.commit()
            except Exception:
                pass
            return res

    # 2) Total failure
    return DownloadResult(ok=False, track_id=track_id, artist=artist, title=title,
                          error="no usable formats across cached+alternatives (try logged-in cookies or different client)")


# ===========================
# Batch helper
# ===========================

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
