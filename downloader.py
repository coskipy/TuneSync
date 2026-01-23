# downloader.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import shutil
import time
import threading
from typing import Optional, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from yt_dlp import YoutubeDL

from db import get_conn

# Thread-safe print lock for parallel downloads
_print_lock = threading.Lock()


# ---------------------------
# Config / helpers
# ---------------------------

REKORDBOX_AUDIO_EXTS = {"mp3", "m4a", "aac", "wav", "aiff", "flac", "alac"}
ALWAYS_TRANSCODE_TO = "m4a"  # Set to None to keep downloads as-is, or "m4a" to always convert to m4a

def _sanitize(name: str) -> str:
    """Sanitize filename while preserving artist names like 'fred again..'
    
    Only removes truly filesystem-unsafe characters, keeps everything else including:
    - Multiple periods (for artists like "fred again..")
    - Commas, parentheses, brackets
    - Spaces and normal punctuation
    """
    # Replace filesystem-unsafe characters with underscore
    # Windows: \ / : * ? " < > |
    # These are the ONLY characters we need to replace
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    
    # Normalize multiple spaces to single space
    name = re.sub(r"\s+", " ", name).strip()
    
    # Windows doesn't allow filenames ending with period
    # But "fred again.." in middle is fine, e.g. "fred again.. - Billie" is OK
    # Only strip trailing periods if they're at the very end
    while name.endswith("."):
        name = name[:-1]
    
    return name.strip()

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
    
    # Token matching in title
    for token in t_title.split():
        if token in c_title:
            score += 1.0
    
    # Artist matching - handle multiple artists separated by comma or &
    # Split on common separators and check each artist
    artist_parts = []
    for sep in [',', '&', ' and ', ' x ', ' vs ', ' feat', ' ft']:
        if sep in artist.lower():
            artist_parts = [a.strip() for a in artist.replace(sep, ',').split(',')]
            break
    if not artist_parts:
        artist_parts = [artist]
    
    # Check if ANY of the artists appear in the title or uploader
    artist_match_count = 0
    for artist_part in artist_parts:
        norm_artist = _norm(artist_part)
        if norm_artist and len(norm_artist) > 2:  # Avoid matching single letters
            matched_this_artist = False
            if norm_artist in c_title:
                score += 2.0  # Strong signal - artist in title
                matched_this_artist = True
            if norm_artist in c_up:
                score += 3.0  # Very strong signal - artist is uploader
                matched_this_artist = True
            
            # Count this artist as matched if found in EITHER title or uploader
            if matched_this_artist:
                artist_match_count += 1
    
    # PENALTY if NO artists matched at all - this is likely the wrong song!
    if len(artist_parts) > 0 and artist_match_count == 0:
        score -= 10.0  # Heavy penalty for no artist match
    
    # Boost for official/verified sources - HEAVILY favor Topic channels
    if "topic" in c_up:
        score += 5.0  # Topic channels are auto-generated OFFICIAL sources - very reliable
    elif "vevo" in c_up:
        score += 2.0  # Vevo is also official but less common
    elif "official" in c_title.lower() or "official" in c_up:
        score += 1.5
    else:
        # User uploads get a penalty
        score -= 1.0
    
    # Boost exact or near-exact title matches
    if t_title == c_title:
        score += 3.0
    elif t_title in c_title or c_title in t_title:
        score += 2.0
    
    # Smart artist matching penalty for remixes/collaborations
    # If Spotify has multiple artists (e.g., "Artist A, Artist B"), 
    # but the upload only shows one artist, it's likely a different version
    if len(artist_parts) >= 2:
        # Multiple artists on Spotify - likely a remix or collaboration
        # If we only matched 1 artist, heavily penalize (probably the original, not the remix)
        if artist_match_count == 1:
            score -= 10.0  # HEAVY penalty for missing collaborator
        elif artist_match_count == 0:
            score -= 15.0  # Even worse - no artists matched
    elif len(artist_parts) == 1 and artist_match_count == 0:
        # Single artist but no match - wrong song entirely
        score -= 15.0
    
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

def _pick_best(entries: List[Dict[str, Any]], artist: str, title: str, target: Optional[float], release_date: Optional[str] = None, debug: bool = False) -> Optional[Dict[str, Any]]:
    if not entries:
        return None
    
    if debug:
        print(f"\n🔍 Search results for '{artist} - {title}' (target: {target}s if target else 'unknown'):")
        print(f"   Found {len(entries)} results")
        if release_date:
            print(f"   📅 Spotify release date: {release_date}")
    
    best, best_score = None, -1e9
    candidates = []  # Track all candidates with scores for logging
    
    # Parse Spotify release date if provided (format: YYYY-MM-DD or YYYY-MM or YYYY)
    min_upload_date = None
    if release_date:
        from datetime import datetime, timedelta
        try:
            # Parse various date formats from Spotify
            if len(release_date) == 4:  # YYYY
                spotify_date = datetime.strptime(release_date, "%Y")
            elif len(release_date) == 7:  # YYYY-MM
                spotify_date = datetime.strptime(release_date, "%Y-%m")
            else:  # YYYY-MM-DD
                spotify_date = datetime.strptime(release_date, "%Y-%m-%d")
            
            # Subtract 1 month buffer (to allow for early platform releases)
            min_upload_date = spotify_date - timedelta(days=30)
            if debug:
                print(f"   � Filtering uploads before {min_upload_date.strftime('%Y-%m-%d')} (Spotify release: {release_date})")
        except Exception as ex:
            if debug:
                print(f"   ⚠️  Failed to parse release date '{release_date}': {ex}")
            pass  # If date parsing fails, skip filtering
    
    for e in entries:
        if not e:
            continue
        dur = e.get("duration")
        e_title = (e.get("title") or "")
        # Handle null uploaders (official YouTube channels often show as None in search results)
        e_uploader = (e.get("uploader") or e.get("channel") or e.get("channel_id") or "Unknown")
        
        # Duration filter: Hard cutoffs only
        if isinstance(dur, (int, float)):
            # Reject anything under 1 minute - likely a preview/snippet
            if dur < 60:
                if debug:
                    print(f"   ❌ SKIP (too short): {e_title} ({dur}s) - {e_uploader}")
                continue
            # Reject anything over 12 minutes - likely a mix/podcast/album
            if dur > 720:  # 12 minutes
                if debug:
                    print(f"   ❌ SKIP (too long): {e_title} ({dur}s) - {e_uploader}")
                continue

        # Upload date filter: Reject anything uploaded before Spotify release date (minus 1 month buffer)
        # SoundCloud provides 'timestamp', YouTube provides 'upload_date'
        if min_upload_date:
            upload_date = None
            
            # Try timestamp first (SoundCloud in flat extraction)
            timestamp = e.get("timestamp")
            if timestamp:
                try:
                    from datetime import datetime
                    upload_date = datetime.fromtimestamp(timestamp)
                except:
                    pass
            
            # Fallback to upload_date (YouTube full extraction only)
            if not upload_date:
                upload_date_str = e.get("upload_date")  # Format: YYYYMMDD
                if upload_date_str:
                    try:
                        from datetime import datetime
                        upload_date = datetime.strptime(str(upload_date_str), "%Y%m%d")
                    except:
                        pass
            
            # Apply filter if we got a date
            if upload_date and upload_date < min_upload_date:
                if debug:
                    print(f"   ❌ SKIP (too old): {e_title} (uploaded {upload_date.strftime('%Y-%m-%d')}, before {min_upload_date.strftime('%Y-%m-%d')}) - {e_uploader}")
                continue

        e_title_lower = e_title.lower()
        e_uploader_lower = e_uploader.lower()
        
        # Filter out problematic content types using word boundaries to avoid false positives
        # \b matches word boundaries so "live" won't match inside "Oliver"
        # Note: "radio edit" is ALLOWED as it's the standard version for electronic music
        bad_keywords = [
            r"\blive\b", r"\blive at\b", r"\blive from\b", r"\bconcert\b",
            r"\bkaraoke\b", r"\binstrumental\b", r"\bbacking track\b",
            r"\bcover\b", r"\bcovered by\b",
            r"\bdj set\b", r"\bdj mix\b", r"\bmix set\b", r"\bcontinuous mix\b",
            r"\bnightcore\b", r"\bslowed\b", r"\breverb\b", r"\bsped up\b",
            r"\bacoustic\b", r"\bunplugged\b",
            r"\bparody\b", r"\bspoof\b",
            r"\btutorial\b", r"\bhow to play\b", r"\blesson\b",
            r"\breaction\b", r"\breview\b",
            r"\blyrics\b", r"\blyric video\b", r"\bletra\b",
        ]
        
        # Check if title contains problematic keywords (case-insensitive regex)
        has_bad_keyword = False
        for pattern in bad_keywords:
            match = re.search(pattern, e_title_lower, re.IGNORECASE)
            if match:
                keyword = match.group(0)
                # Exception: if the original track title also has this keyword, allow it
                if not re.search(pattern, title.lower(), re.IGNORECASE):
                    has_bad_keyword = True
                    break
        
        if has_bad_keyword:
            continue

        s = _title_score(artist, title, e_title, e_uploader)
        
        # No duration penalty - extended mixes are valid!
        # Hard cutoffs (< 1min, > 12min) are applied in filtering above
        
        # Store candidate with base score (before view/sub adjustments)
        view_count = e.get('view_count', 0) or 0
        subscriber_count = e.get('channel_follower_count', 0) or 0
        
        candidates.append({
            'entry': e,
            'title': e_title,
            'uploader': e_uploader,
            'duration': dur,
            'base_score': s,
            'url': e.get('webpage_url') or e.get('url'),
            'views': view_count,
            'subs': subscriber_count
        })
    
    # Now apply relative scoring based on view/sub counts among candidates
    if candidates:
        max_views = max(c['views'] for c in candidates)
        max_subs = max(c['subs'] for c in candidates)
        
        for c in candidates:
            # Relative view count bonus: up to +3.0 for the most viewed
            if max_views > 0 and c['views'] > 0:
                view_ratio = c['views'] / max_views
                # Give significant bonus to top viewed results
                c['view_bonus'] = view_ratio * 3.0
            else:
                c['view_bonus'] = 0.0
            
            # Relative subscriber bonus: up to +2.0 for largest channel
            if max_subs > 0 and c['subs'] > 0:
                sub_ratio = c['subs'] / max_subs
                c['sub_bonus'] = sub_ratio * 2.0
            else:
                c['sub_bonus'] = 0.0
            
            # Final score
            c['score'] = c['base_score'] + c['view_bonus'] + c['sub_bonus']
    
    # Find best candidate
    best, best_score = None, -1e9
    for c in candidates:
        if c['score'] > best_score:
            best, best_score = c['entry'], c['score']
    
    # Sort candidates by final score and show top results
    candidates.sort(key=lambda x: x['score'], reverse=True)
    
    if debug and candidates:
        print(f"\n   📊 Top candidates (by score):")
        for i, c in enumerate(candidates[:5], 1):
            marker = "✅ CHOSEN" if i == 1 else f"   #{i}"
            views_str = f"{c['views']:,}" if c['views'] > 0 else "?"
            subs_str = f"{c['subs']:,}" if c['subs'] > 0 else "?"
            # Show score breakdown
            score_detail = f"base={c['base_score']:.1f}"
            if c.get('view_bonus', 0) > 0:
                score_detail += f" +views={c['view_bonus']:.1f}"
            if c.get('sub_bonus', 0) > 0:
                score_detail += f" +subs={c['sub_bonus']:.1f}"
            print(f"   {marker}: [{c['score']:.1f}] {c['title']} ({c['duration']}s)")
            print(f"        👤 {c['uploader']} | 👁️ {views_str} views | 📊 {subs_str} subs")
            print(f"        📈 Score: {score_detail}")
    
    if best:
        best_candidate = candidates[0] if candidates else None
        if best_candidate and not debug:
            print(f"   ✅ Selected: {best_candidate['title']} - {best_candidate['uploader']} ({best_candidate['views']:,} views)")
    
    return best

def _search_best(artist: str, title: str, duration_ms: Optional[int], release_date: Optional[str] = None, isrc: Optional[str] = None, debug: bool = False, progress: bool = True) -> Optional[Dict[str, Any]]:
    """
    Tiered search strategy:
      1) ISRC search on YouTube (exact match if available)
      2) SoundCloud with date filtering for official uploads
      3) Multi-source search with scoring (fallback for everything else)
    """
    target = (duration_ms or 0) / 1000.0 if duration_ms else None

    opts_flat = {
        "quiet": True,
        "noplaylist": True,
        "default_search": "auto",
        "skip_download": True,
        "extract_flat": True,
        # Workaround for YouTube 403 errors (see: https://github.com/yt-dlp/yt-dlp/issues/14680)
        # Use actual player version to avoid pinned player issues
        "extractor_args": {"youtube": {"player_js_version": ["actual"]}},
        # Suppress expected warnings about SABR/missing formats
        "no_warnings": True,
    }

    # ========== TIER 1: ISRC Search on YouTube ========== 
    if isrc:
        if progress:
            print(f"\r  Searching... ISRC", end="", flush=True)
        
        with YoutubeDL(opts_flat) as ydl:
            try:
                info = ydl.extract_info(f"ytsearch3:{isrc}", download=False)
                if info:
                    entries = info.get("entries") or []
                    if entries:
                        if debug:
                            print(f"\n   ✅ Found via ISRC: {entries[0].get('title')} by {entries[0].get('uploader')}")
                        elif progress:
                            print(f"\r  Found via ISRC ✓", end="", flush=True)
                        return entries[0]
            except Exception:
                pass
        
        if debug:
            print(f"   ⚠️  No ISRC results")

    # ========== TIER 2: SoundCloud with Date Filtering ==========
    if progress:
        print(f"\r  Searching... Tier 2", end="", flush=True)
    
    if debug:
        print(f"\n   🎵 Tier 2: Trying SoundCloud with date filtering")
    
    sc_entries = []
    sc_qvars = [
        f'scsearch6:"{artist}" "{title}"',
        f'scsearch6:{artist} - {title}',
    ]
    
    with YoutubeDL(opts_flat) as ydl:
        for q in sc_qvars:
            try:
                info = ydl.extract_info(q, download=False)
                if info:
                    entries = info.get("entries") or []
                    sc_entries.extend(entries)
            except Exception:
                continue
    
    # Filter SC results by release date and find official uploads
    sc_official_candidates = []
    
    if sc_entries and release_date:
        from datetime import datetime, timedelta
        try:
            # Parse release date
            if len(release_date) == 4:
                spotify_date = datetime.strptime(release_date, "%Y")
            elif len(release_date) == 7:
                spotify_date = datetime.strptime(release_date, "%Y-%m")
            else:
                spotify_date = datetime.strptime(release_date, "%Y-%m-%d")
            
            min_upload_date = spotify_date - timedelta(days=30)
            
            # Parse artist names for matching
            artist_parts = []
            artist_lower = artist.lower()
            for sep in [',', '&', ' and ', ' x ', ' vs ', ' feat', ' ft']:
                if sep in artist_lower:
                    artist_parts = [a.strip() for a in artist_lower.replace(sep, ',').split(',')]
                    break
            if not artist_parts:
                artist_parts = [artist_lower]
            
            # Filter and check for official uploads
            for e in sc_entries:
                timestamp = e.get("timestamp")
                if not timestamp:
                    continue
                
                upload_date = datetime.fromtimestamp(timestamp)
                if upload_date < min_upload_date:
                    continue  # Too old
                
                # Check if this is from an official artist account
                uploader = (e.get("uploader") or "").lower()
                duration = e.get("duration", 0)
                
                if duration < 60:  # Skip previews
                    continue
                
                # Check if uploader matches any artist
                is_official = False
                for artist_part in artist_parts:
                    norm_artist = artist_part.replace('.', '').replace(' ', '')
                    norm_uploader = uploader.replace('.', '').replace(' ', '')
                    if norm_artist in norm_uploader:
                        is_official = True
                        break
                
                if is_official:
                    sc_official_candidates.append(e)
            
            # If we found official SC uploads with correct date, return best one
            if sc_official_candidates:
                if progress:
                    print(f"\r  Found via SoundCloud ✓", end="", flush=True)
                if debug:
                    print(f"\n   ✅ Found {len(sc_official_candidates)} official SoundCloud upload(s)")
                return _pick_best(sc_official_candidates, artist, title, target, release_date=release_date, debug=debug)
        
        except Exception as ex:
            if debug:
                print(f"   ⚠️  Tier 2 date parsing failed: {ex}")
    
    # ============================================================
    # TIER 3: Multi-source fallback with all existing scoring
    # ============================================================
    if progress:
        print(f"\r  Searching... Tier 3", end="", flush=True)
    
    if debug:
        print(f"\n   🌐 Tier 3: Multi-source search (SoundCloud + YouTube)")
    
    all_entries = []
    
    # Collect all SoundCloud results (not just official)
    all_entries.extend(sc_entries)
    
    # Collect YouTube results (flat extraction - fast)
    yt_queries = [
        f'ytsearch15:"{artist}" "{title}"',  # Quoted search
        f'ytsearch10:{artist} - {title}',    # Simple search
    ]
    
    with YoutubeDL(opts_flat) as ydl:
        for q in yt_queries:
            try:
                info = ydl.extract_info(q, download=False)
                if info:
                    entries = info.get("entries") or ([info] if info.get("_type") != "playlist" else [])
                    all_entries.extend(entries)
            except Exception:
                continue
    
    # Use _pick_best with all existing scoring logic
    if all_entries:
        if progress:
            print(f"\r  Found via search ✓   ", end="", flush=True)
        if debug:
            print(f"\n   📊 Tier 3: Scoring {len(all_entries)} candidates")
        return _pick_best(all_entries, artist, title, target, release_date=release_date, debug=debug)
    
    # No results found at all
    return None


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
        "-vn",  # No video
        "-c:a", "aac",  # AAC audio codec
        "-b:a", f"{aac_kbps}k",  # Bitrate
        "-ar", "44100",  # Sample rate (standard for music)
        "-af", "aresample=resampler=soxr",  # High-quality resampling
        "-avoid_negative_ts", "make_zero",  # Fix timestamp issues at start
        "-fflags", "+bitexact+genpts",  # Consistent output + regenerate timestamps
        "-movflags", "+faststart",  # Optimize for streaming
        "-write_xing", "0",  # Don't write Xing header (can cause start issues)
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
            # Update same line with progress
            line = f"\r  [{pct:5.1f}%]"
            print(line, end="", flush=True)
        elif status == "finished":
            # Clear the percentage, will be updated with final status
            print("\r  ", end="", flush=True)
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
    release_date: Optional[str] = None,
    isrc: Optional[str] = None,
    out_root: Path,
    aac_kbps: int = 192,
    retries: int = 2,
    progress: bool = True,
    debug: bool = False
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
        # Always include track_id in filename to guarantee uniqueness
        base = f"{_sanitize(label)} [{track_id[:8]}]"
        conn = get_conn()

        # Short-circuit if a compatible file already exists
        # Note: We used to check duration here and delete mismatches, but this caused infinite loops
        # when YouTube only has versions with slightly different durations. Better to keep what we have
        # than endlessly re-download the same file.
        for ext in ("m4a", "mp3", "flac", "wav", "aiff", "alac", "aac", "webm", "opus"):
            p = (out_root / base).with_suffix(f".{ext}")
            if p.exists():
                if progress:
                    print(f"\r  {label}: Already exists ✓                    ")
                return DownloadResult(ok=True, track_id=track_id, artist=artist, title=title, final_path=p, source_url=None, ext=ext, abr_kbps=None, transcoded=False)

        hooks = [_make_progress_hook(label)] if progress else []

        # Always search fresh (no caching to avoid infinite loops with bad sources)
        if progress:
            print(f"\r  {label}: Searching...", end="", flush=True)

        def _do_download(u: str) -> DownloadResult:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "ignoreerrors": True,  # Suppress transient errors during retries
                "noplaylist": True,
                "continuedl": True,
                # Reduce concurrent downloads to avoid m3u8 fragment race conditions
                "concurrent_fragment_downloads": 1,
                "retries": 5,
                "fragment_retries": 10,
                "overwrites": False,
                "noprogress": True,
                # Format priority: 140 (m4a 128kbps AAC, highest quality audio-only) first
                # Then 251 (webm/opus 160kbps), bestaudio, 139 (m4a 48kbps low quality)
                # yt-dlp 2025.11.12+ uses Deno/JS runtime to solve YouTube challenges
                # m3u8 formats (91-96) kept as last resort backup in case YouTube implements new restrictions
                # Format 91 = 144p (~1.36MB), 92 = 240p (~1.45MB), good for Topic channels with static images
                "format": "140/251/bestaudio/139/91/92/93/94/95/96",
                "outtmpl": str(out_root / f"{base}.%(ext)s"),
                "postprocessors": [],
                "progress_hooks": hooks,
                "cookiesfrombrowser": ["chrome"],  # Extract cookies from Chrome for SoundCloud auth
                # Sleep between retries to avoid rate limiting
                "sleep_interval": 1,
                "max_sleep_interval": 3,
            }
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(u, download=True)
                if info.get("_type") == "playlist" and info.get("entries"):
                    info = info["entries"][0]

                # Get the actual filename yt-dlp created (this is the source of truth!)
                # yt-dlp stores the final filepath in info dict after download
                actual_filepath = info.get("_filename") or info.get("filepath")
                
                if actual_filepath and Path(actual_filepath).exists():
                    # Use the file yt-dlp actually created
                    final = Path(actual_filepath)
                else:
                    # Fallback: search by track_id only (most reliable)
                    track_id_short = track_id[:8]
                    final = None
                    
                    # Wait a moment for filesystem to catch up, then search by track_id
                    for retry in range(8):
                        for audio_ext in ("m4a", "mp3", "opus", "webm", "aac", "mp4", "flac", "wav"):
                            # Search for ANY file containing our track_id
                            pattern = f"*[{track_id_short}]*.{audio_ext}"
                            matches = list(out_root.glob(pattern))
                            if matches:
                                # Take the most recently created matching file
                                found = max(matches, key=lambda p: p.stat().st_mtime)
                                # Rename to our expected format for consistency
                                expected_name = f"{base}.{audio_ext}"
                                expected_path = out_root / expected_name
                                if found != expected_path:
                                    try:
                                        found.rename(expected_path)
                                        final = expected_path
                                    except OSError:
                                        # If rename fails, use what we found
                                        final = found
                                else:
                                    final = found
                                break
                        
                        if final:
                            break
                        time.sleep(0.5)
                    
                    if not final:
                        # Debug: show what we were looking for
                        debug_msg = f"Download reported success but file not found. Expected: {base}.* in {out_root}"
                        return DownloadResult(ok=False, track_id=track_id, artist=artist, title=title,
                                              source_url=u,
                                              error=debug_msg)

                # Check if the downloaded file is a preview (< 60 seconds)
                actual_duration = _safe_duration_seconds(final)
                if actual_duration and actual_duration < 60:
                    # Delete the preview file
                    try:
                        final.unlink()
                    except OSError:
                        pass
                    return DownloadResult(ok=False, track_id=track_id, artist=artist, title=title,
                                          source_url=u,
                                          error="Only preview found (< 60 seconds)")

                ext = final.suffix.lstrip(".").lower()
                if ext in REKORDBOX_AUDIO_EXTS:
                    abr = int(info["abr"]) if isinstance(info.get("abr"), (int, float)) else None
                    
                    # If configured to always transcode, check if we need to
                    # Skip transcoding if already in a good format (m4a or mp3)
                    if ALWAYS_TRANSCODE_TO and ext not in (ALWAYS_TRANSCODE_TO, "mp3"):
                        if progress:
                            print(f"\r  ", end="", flush=True)  # Clear search status
                        m4a_path = out_root / f"{base}.{ALWAYS_TRANSCODE_TO}"
                        _ffmpeg_transcode_to_m4a(final, m4a_path, aac_kbps=aac_kbps)
                        
                        # Delete the original file after successful transcode
                        try:
                            final.unlink()
                        except OSError:
                            pass  # Ignore deletion errors
                        
                        if progress:
                            print(f"\r  {label}: Complete ✓ (transcoded)                    ")
                        return DownloadResult(ok=True, track_id=track_id, artist=artist, title=title,
                                              final_path=m4a_path,
                                              source_url=u, ext=ALWAYS_TRANSCODE_TO, abr_kbps=aac_kbps, transcoded=True)
                    else:
                        # Keep as-is (already m4a or mp3)
                        if progress:
                            print(f"\r  {label}: Complete ✓                    ")
                        return DownloadResult(ok=True, track_id=track_id, artist=artist, title=title,
                                              final_path=final,
                                              source_url=u, ext=ext, abr_kbps=abr, transcoded=False)

                # If file is not compatible (opus, webm, mp4, etc.), must transcode
                if progress:
                    print(f"\r  ", end="", flush=True)  # Clear search status
                m4a_path = out_root / f"{base}.m4a"
                _ffmpeg_transcode_to_m4a(final, m4a_path, aac_kbps=aac_kbps)
                
                # Delete the original file after successful transcode
                try:
                    final.unlink()
                except OSError:
                    pass  # Ignore deletion errors
                
                if progress:
                    print(f"\r  {label}: Complete ✓ (transcoded)                    ")
                return DownloadResult(ok=True, track_id=track_id, artist=artist, title=title,
                                      final_path=m4a_path,
                                      source_url=u, ext="m4a", abr_kbps=aac_kbps, transcoded=True)

        # Fresh search (always - no caching to avoid infinite loops with bad sources)
        chosen = _search_best(artist, title, duration_ms, release_date=release_date, isrc=isrc, debug=debug, progress=progress)
        if not chosen:
            if progress:
                print(f"\r  {label}: Not found ✗                    ")
            return DownloadResult(ok=False, track_id=track_id, artist=artist, title=title, error="No suitable YouTube match found")

        url = chosen.get("webpage_url") or chosen.get("url")
        if not url:
            if progress:
                print(f"\r  {label}: Missing URL ✗                    ")
            return DownloadResult(ok=False, track_id=track_id, artist=artist, title=title, error="Selected entry has no URL")

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
    progress: bool = True,
    max_workers: int = 4,
    debug: bool = False
) -> List[DownloadResult]:
    """
    Download missing tracks in parallel using a thread pool.
    
    Args:
        rows: List of track metadata dicts
        out_root: Root directory for downloads
        aac_kbps: Audio bitrate for transcoding
        progress: Show progress output
        max_workers: Number of parallel download threads (default 4)
        debug: Show detailed search results and scoring
    """
    total = len(rows)
    completed = 0
    
    def _download_wrapper(idx: int, r: Dict[str, Any]) -> tuple[int, DownloadResult]:
        """Wrapper to track which track is being downloaded."""
        nonlocal completed
        
        # Disable individual track progress in parallel mode
        res = download_track(
            track_id=r["id"],
            artist=r["artist"],
            title=r["name"],
            duration_ms=r["duration_ms"],
            release_date=r["release_date"] if "release_date" in r.keys() else None,
            isrc=r["isrc"] if "isrc" in r.keys() else None,
            out_root=out_root,
            aac_kbps=aac_kbps,
            progress=False,  # Disable per-track progress to avoid overlap
            debug=debug
        )
        
        # Thread-safe status update
        with _print_lock:
            completed += 1
            status = "✓" if res.ok else "✗"
            label = f"{r['artist']} - {r['name']}"
            if len(label) > 60:
                label = label[:57] + "..."
            print(f"  [{completed:>{len(str(total))}}/{total}] {status} {label}")
        
        return (idx, res)
    
    results: List[DownloadResult] = [None] * total
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_download_wrapper, idx, r): idx
            for idx, r in enumerate(rows, 1)
        }
        
        for future in as_completed(futures):
            try:
                idx, res = future.result()
                results[idx - 1] = res
            except Exception as e:
                idx = futures[future]
                results[idx - 1] = DownloadResult(
                    ok=False,
                    track_id=rows[idx - 1].get("id"),
                    artist=rows[idx - 1].get("artist"),
                    title=rows[idx - 1].get("name"),
                    error=f"Thread error: {str(e)}"
                )
    
    return results


def download_track_from_url(
    *,
    url: str,
    track_id: str,
    artist: str,
    title: str,
    out_root: Path,
    aac_kbps: int = 192,
    progress: bool = True,
) -> DownloadResult:
    """Download from an explicit URL (YouTube/SoundCloud/etc.) and save using the normal naming scheme."""
    if not _have_ffmpeg():
        return DownloadResult(ok=False, track_id=track_id, artist=artist, title=title, error="ffmpeg not found on PATH")

    label = f"{artist} - {title}"
    try:
        out_root.mkdir(parents=True, exist_ok=True)
        base = f"{_sanitize(label)} [{track_id[:8]}]"

        # Short-circuit if already exists.
        for ext in ("m4a", "mp3", "flac", "wav", "aiff", "alac", "aac", "webm", "opus"):
            p = (out_root / base).with_suffix(f".{ext}")
            if p.exists():
                return DownloadResult(ok=True, track_id=track_id, artist=artist, title=title, final_path=p, source_url=url, ext=ext)

        hooks = [_make_progress_hook(label)] if progress else []
        if progress:
            print(f"\r  {label}: Downloading…", end="", flush=True)

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
            "noplaylist": True,
            "continuedl": True,
            "concurrent_fragment_downloads": 1,
            "retries": 5,
            "fragment_retries": 10,
            "overwrites": False,
            "noprogress": True,
            "format": "140/251/bestaudio/139/91/92/93/94/95/96",
            "outtmpl": str(out_root / f"{base}.%(ext)s"),
            "postprocessors": [],
            "progress_hooks": hooks,
            "cookiesfrombrowser": ["chrome"],
            "sleep_interval": 1,
            "max_sleep_interval": 3,
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                return DownloadResult(ok=False, track_id=track_id, artist=artist, title=title, source_url=url, error="Download failed")
            if info.get("_type") == "playlist" and info.get("entries"):
                info = info["entries"][0]

            actual_filepath = info.get("_filename") or info.get("filepath")
            final: Optional[Path]
            if actual_filepath and Path(actual_filepath).exists():
                final = Path(actual_filepath)
            else:
                # Fallback: locate by track id in output folder.
                track_id_short = track_id[:8]
                final = None
                for _retry in range(8):
                    for audio_ext in ("m4a", "mp3", "opus", "webm", "aac", "mp4", "flac", "wav"):
                        pattern = f"*[{track_id_short}]*.{audio_ext}"
                        matches = list(out_root.glob(pattern))
                        if matches:
                            found = max(matches, key=lambda p: p.stat().st_mtime)
                            expected = out_root / f"{base}.{audio_ext}"
                            if found != expected:
                                try:
                                    found.rename(expected)
                                    final = expected
                                except OSError:
                                    final = found
                            else:
                                final = found
                            break
                    if final:
                        break
                    time.sleep(0.5)

            if not final:
                return DownloadResult(ok=False, track_id=track_id, artist=artist, title=title, source_url=url, error="Download reported success but file not found")

            # Preview guard.
            actual_duration = _safe_duration_seconds(final)
            if actual_duration and actual_duration < 60:
                try:
                    final.unlink()
                except OSError:
                    pass
                return DownloadResult(ok=False, track_id=track_id, artist=artist, title=title, source_url=url, error="Only preview found (< 60 seconds)")

            ext = final.suffix.lstrip(".").lower()
            if ext in REKORDBOX_AUDIO_EXTS:
                abr = int(info["abr"]) if isinstance(info.get("abr"), (int, float)) else None
                if ALWAYS_TRANSCODE_TO and ext not in (ALWAYS_TRANSCODE_TO, "mp3"):
                    m4a_path = out_root / f"{base}.{ALWAYS_TRANSCODE_TO}"
                    _ffmpeg_transcode_to_m4a(final, m4a_path, aac_kbps=aac_kbps)
                    try:
                        final.unlink()
                    except OSError:
                        pass
                    return DownloadResult(ok=True, track_id=track_id, artist=artist, title=title, final_path=m4a_path, source_url=url, ext=ALWAYS_TRANSCODE_TO, abr_kbps=aac_kbps, transcoded=True)

                return DownloadResult(ok=True, track_id=track_id, artist=artist, title=title, final_path=final, source_url=url, ext=ext, abr_kbps=abr, transcoded=False)

            # Not Rekordbox compatible, transcode.
            m4a_path = out_root / f"{base}.m4a"
            _ffmpeg_transcode_to_m4a(final, m4a_path, aac_kbps=aac_kbps)
            try:
                final.unlink()
            except OSError:
                pass
            return DownloadResult(ok=True, track_id=track_id, artist=artist, title=title, final_path=m4a_path, source_url=url, ext="m4a", abr_kbps=aac_kbps, transcoded=True)

    except Exception as e:
        return DownloadResult(ok=False, track_id=track_id, artist=artist, title=title, source_url=url, error=_classify_error(str(e)))
