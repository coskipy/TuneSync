# spotify_client.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional
from time import sleep
import time
import threading
import json
import os
import subprocess
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyPKCE
from spotipy.exceptions import SpotifyException
from spotipy.cache_handler import CacheHandler, CacheFileHandler


@dataclass
class _Cache:
    artist_genres: Dict[str, List[str]]


class SpotifyClient:
    sp: Optional[spotipy.Spotify] = None  # class-wide client
    cache = _Cache(artist_genres={})
    _request_lock = threading.Lock()  # Rate limit protection
    _min_request_delay = 0.1  # Minimum seconds between requests (10 req/sec)
    _auth_open_browser: Optional[bool] = None
    _auth_scope: Optional[str] = None

    _KC_SERVICE = "TuneSync Spotify Token"
    _KC_ACCOUNT = "default"

    # PKCE does not require a client secret; a client ID + redirect URI are enough.
    # These defaults make the packaged app work even when it cannot see the repo-local .env.
    _DEFAULT_CLIENT_ID = "cde55a79f546483bad4e30ec92c7c45b"
    # NOTE: 5000 is commonly occupied on macOS (e.g. Control Center), so prefer a higher port.
    _DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/callback"
    _LEGACY_REDIRECT_URI = "http://127.0.0.1:5000/callback"
    _REDIRECT_CANDIDATES = (
        _DEFAULT_REDIRECT_URI,
        _LEGACY_REDIRECT_URI,
    )

    @staticmethod
    def _debug_enabled() -> bool:
        return (os.getenv("TUNESYNC_DEBUG") or "").strip().lower() in {"1", "true", "yes"}

    @staticmethod
    def _default_app_data_dir() -> "Path":
        from pathlib import Path
        import sys

        try:
            if sys.platform == "darwin":
                return (Path.home() / "Library" / "Application Support" / "TuneSync")
        except Exception:
            pass
        return Path.home() / ".tunesync"

    @classmethod
    def _load_env(cls) -> None:
        """Load configuration from likely locations (dev + packaged)."""
        try:
            from pathlib import Path

            # Packaged-friendly location.
            app_env = cls._default_app_data_dir() / ".env"
            if app_env.exists():
                load_dotenv(dotenv_path=str(app_env), override=False)

            # Dev-friendly location (repo root / current working directory).
            cwd_env = Path(".env")
            if cwd_env.exists():
                load_dotenv(dotenv_path=str(cwd_env), override=False)
        except Exception:
            try:
                load_dotenv(override=False)
            except Exception:
                pass

    @staticmethod
    def _is_port_in_use(port: int) -> bool:
        import socket

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                return s.connect_ex(("127.0.0.1", int(port))) == 0
        except Exception:
            return False

    @staticmethod
    def _extract_port(redirect_uri: str) -> Optional[int]:
        try:
            parsed = urlparse(redirect_uri)
            return int(parsed.port) if parsed.port is not None else None
        except Exception:
            return None

    @classmethod
    def _pick_available_redirect_uri(cls, preferred_uri: str) -> str:
        ordered: List[str] = []
        for candidate in [preferred_uri, *cls._REDIRECT_CANDIDATES]:
            c = (candidate or "").strip()
            if c and c not in ordered:
                ordered.append(c)

        for candidate in ordered:
            port = cls._extract_port(candidate)
            if port is None:
                return candidate
            if not cls._is_port_in_use(port):
                return candidate

        return preferred_uri

    @classmethod
    def _normalize_redirect_uri(cls, redirect_uri: str | None) -> str:
        uri = (redirect_uri or "").strip()
        if not uri:
            uri = cls._DEFAULT_REDIRECT_URI

        picked = cls._pick_available_redirect_uri(uri)
        if picked != uri:
            cls._dbg(
                f"redirect port busy for {uri}; using {picked} instead"
            )
        return picked

    @classmethod
    def _patch_auth_opener_if_needed(cls, auth: SpotifyPKCE, *, open_browser: bool) -> None:
        # On macOS frozen apps, `webbrowser.open()` can silently return False and do nothing.
        # Spotipy doesn't check the return value, so override its opener to use `/usr/bin/open`.
        try:
            is_frozen = bool(getattr(sys, "frozen", False))
            if open_browser and sys.platform == "darwin" and is_frozen:
                def _open_auth_url(state=None):
                    url = auth.get_authorize_url(state)
                    try:
                        subprocess.Popen(["/usr/bin/open", url])
                    except Exception:
                        try:
                            import webbrowser

                            webbrowser.open(url)
                        except Exception:
                            pass

                auth._open_auth_url = _open_auth_url  # type: ignore[attr-defined]
        except Exception:
            pass

    @classmethod
    def _dbg(cls, msg: str) -> None:
        if not cls._debug_enabled():
            return
        try:
            print(f"[spotify] {msg}", flush=True)
        except Exception:
            pass

    @classmethod
    def login(
        cls,
        scope: str = "playlist-read-private playlist-read-collaborative",
        silent: bool = False,
    ) -> spotipy.Spotify:
        cls._load_env()
        client_id = (os.getenv("SPOTIPY_CLIENT_ID") or "").strip() or None
        redirect_uri = cls._normalize_redirect_uri(os.getenv("SPOTIPY_REDIRECT_URI"))

        if not client_id:
            client_id = cls._DEFAULT_CLIENT_ID
        if not client_id or not redirect_uri:
            raise RuntimeError(
                "Spotify is not configured. Missing SPOTIPY_CLIENT_ID or SPOTIPY_REDIRECT_URI."
            )

        # In the GUI subprocess, never trigger an interactive browser/login flow.
        # If auth is required, fail fast with a clear error instead of hanging.
        ui_mode = (os.getenv("TUNESYNC_UI") or "").strip() == "1"

        cache_handler = _make_cache_handler()
        if ui_mode:
            token = None
            try:
                token = cache_handler.get_cached_token()
            except Exception:
                token = None
            if not (isinstance(token, dict) and token.get("refresh_token")):
                raise RuntimeError(
                    "Spotify login required. In TuneSync, click 'Sign in' to Spotify in Settings to authorize once, "
                    "then retry."
                )

        open_browser = (not bool(silent)) and (not ui_mode)

        if cls.sp is None or cls._auth_open_browser != open_browser or cls._auth_scope != scope:
            cls._dbg(f"init auth ui_mode={ui_mode} open_browser={open_browser} scope={scope}")
            auth = SpotifyPKCE(
                client_id=client_id,
                redirect_uri=redirect_uri,
                scope=scope,
                open_browser=open_browser,
                cache_handler=cache_handler,
            )

            cls._patch_auth_opener_if_needed(auth, open_browser=open_browser)

            # requests_timeout prevents indefinite hangs on network calls.
            cls.sp = spotipy.Spotify(auth_manager=auth, requests_timeout=10)
            cls._auth_open_browser = open_browser
            cls._auth_scope = scope

        # Intentionally no "Logged in as" call/print here.
        # current_user() is an extra API call and can itself hang under bad network conditions.

        return cls.sp

    @classmethod
    def create_pkce_auth(cls, *, scope: str, open_browser: bool) -> SpotifyPKCE:
        """Create a SpotifyPKCE auth manager with our env/default handling.

        This is used by the GUI manual sign-in flow (open in browser + paste redirect URL),
        and by the normal `login()` path.
        """
        cls._load_env()
        client_id = (os.getenv("SPOTIPY_CLIENT_ID") or "").strip() or None
        redirect_uri = cls._normalize_redirect_uri(os.getenv("SPOTIPY_REDIRECT_URI"))

        if not client_id:
            client_id = cls._DEFAULT_CLIENT_ID
        if not client_id or not redirect_uri:
            raise RuntimeError(
                "Spotify is not configured. Missing SPOTIPY_CLIENT_ID or SPOTIPY_REDIRECT_URI."
            )

        cache_handler = _make_cache_handler()
        auth = SpotifyPKCE(
            client_id=client_id,
            redirect_uri=redirect_uri,
            scope=scope,
            open_browser=open_browser,
            cache_handler=cache_handler,
        )
        cls._patch_auth_opener_if_needed(auth, open_browser=open_browser)
        return auth

    @classmethod
    def has_cached_token(cls) -> bool:
        try:
            cls._load_env()
            handler = _make_cache_handler()
            token = handler.get_cached_token()
            return bool(token and isinstance(token, dict))
        except Exception:
            return False

    @classmethod
    def is_authenticated(cls) -> bool:
        """Check if we have a valid token by calling a playlist-scoped endpoint."""
        try:
            sp = cls.login(silent=True)
            # Validate auth using playlist scope (always requested by TuneSync).
            sp.current_user_playlists(limit=1)
            return True
        except Exception:
            # Token is invalid, expired, or revoked - clear it
            try:
                cls.logout()
            except Exception:
                pass
            return False

    @classmethod
    def logout(cls) -> None:
        cls._load_env()
        try:
            handler = _make_cache_handler()
            if hasattr(handler, "delete"):
                handler.delete()  # type: ignore[attr-defined]
        except Exception:
            pass

        # Best-effort hard clear: some cache handlers may not expose delete reliably.
        try:
            handler = _make_cache_handler()
            if hasattr(handler, "save_token_to_cache"):
                handler.save_token_to_cache({})  # type: ignore[attr-defined]
        except Exception:
            pass

        try:
            from pathlib import Path

            cache_dir = cls._default_app_data_dir()
            for p in (
                cache_dir / "spotify_token.json",
                Path(".spotify_token.json"),
            ):
                try:
                    if p.exists():
                        p.unlink()
                except Exception:
                    pass
        except Exception:
            pass

        cls.sp = None
        cls._auth_open_browser = None
        cls._auth_scope = None

    # ------------------------
    # Internal helpers
    # ------------------------
    @classmethod
    def _retry(cls, fn, *args, **kwargs):
        """Retry helper with rate limiting and backoff for rate limits/transient errors."""
        tries = 3
        delay = 1.5
        for i in range(tries):
            started = time.monotonic()
            try:
                # Rate limit protection: serialize requests
                with cls._request_lock:
                    sleep(cls._min_request_delay)
                    out = fn(*args, **kwargs)
                cls._dbg(f"ok {getattr(fn, '__name__', str(fn))} ({(time.monotonic() - started):.2f}s)")
                return out
            except SpotifyException as e:
                cls._dbg(
                    f"err {getattr(fn, '__name__', str(fn))} http={getattr(e, 'http_status', None)} "
                    f"try={i + 1}/{tries} ({(time.monotonic() - started):.2f}s)"
                )
                if (e.http_status in (429, 500, 502, 503, 504)) and i < tries - 1:
                    backoff = delay * (2 ** i)
                    print(f"  ⏱️  Rate limited (HTTP {e.http_status}), waiting {backoff}s...")
                    sleep(backoff)
                    continue
                raise
            except Exception as e:
                cls._dbg(
                    f"err {getattr(fn, '__name__', str(fn))} {type(e).__name__}: {e} "
                    f"try={i + 1}/{tries} ({(time.monotonic() - started):.2f}s)"
                )
                raise

    # ------------------------
    # Playlists
    # ------------------------
    @classmethod
    def get_my_playlists(cls) -> List[Dict]:
        sp = cls.login(silent=True)  # Don't print login message here
        results = cls._retry(sp.current_user_playlists, limit=50)
        playlists: List[Dict] = []
        while results:
            for p in results["items"]:
                owner = p.get("owner") or {}
                creator = owner.get("display_name") or owner.get("id")
                owner_id = owner.get("id")
                public_raw = p.get("public")
                spotify_is_public: Optional[int]
                if public_raw is True:
                    spotify_is_public = 1
                elif public_raw is False:
                    spotify_is_public = 0
                else:
                    spotify_is_public = None
                images = p.get("images") or []
                cover_url = None
                if images:
                    cover_url = sorted(images, key=lambda x: (x.get("width") or 0), reverse=True)[0].get("url")
                playlists.append({
                    "id": p["id"],
                    "name": p["name"],
                    "creator": creator,
                    "spotify_owner_id": owner_id,
                    "spotify_is_public": spotify_is_public,
                    "snapshot_id": p.get("snapshot_id"),
                    "image_url": cover_url,
                })
            results = cls._retry(sp.next, results) if results.get("next") else None
        return playlists

    @classmethod
    def get_current_user(cls) -> Dict:
        """Return the signed-in user's profile (requires a cached token)."""
        sp = cls.login(silent=True)
        return cls._retry(sp.current_user)

    @classmethod
    def get_playlist_metadata(cls, playlist_id: str, *, silent: bool = False) -> Dict:
        sp = cls.login(silent=silent)
        p = cls._retry(
            sp.playlist,
            playlist_id,
            fields="id,name,snapshot_id,public,images,owner(display_name,id)",
        )
        owner = p.get("owner") or {}
        creator = owner.get("display_name") or owner.get("id")
        owner_id = owner.get("id")
        public_raw = p.get("public")
        spotify_is_public: Optional[int]
        if public_raw is True:
            spotify_is_public = 1
        elif public_raw is False:
            spotify_is_public = 0
        else:
            spotify_is_public = None
        images = p.get("images") or []
        cover_url = None
        if images:
            cover_url = sorted(images, key=lambda x: (x.get("width") or 0), reverse=True)[0].get("url")
        return {
            "id": p["id"],
            "name": p["name"],
            "creator": creator,
            "spotify_owner_id": owner_id,
            "spotify_is_public": spotify_is_public,
            "snapshot_id": p.get("snapshot_id"),
            "image_url": cover_url,
        }

    @classmethod
    def get_playlists_metadata_batch(cls, playlist_ids: List[str]) -> Dict[str, Dict]:
        """
        Get metadata for multiple playlists efficiently.
        Returns {playlist_id: {id, name, snapshot_id}}

        Strategy: First try to get them all from get_my_playlists() (1 API call),
        then individually fetch any that weren't in that list (e.g., followed playlists).
        """
        if not playlist_ids:
            return {}

        # First, get all user playlists in one go (handles most cases)
        my_playlists = cls.get_my_playlists()
        result = {p["id"]: p for p in my_playlists if p["id"] in playlist_ids}

        # Fetch any missing ones individually (e.g., followed playlists not owned by user)
        missing_ids = set(playlist_ids) - result.keys()
        for pid in missing_ids:
            try:
                result[pid] = cls.get_playlist_metadata(pid)
            except Exception as e:
                print(f"  ⚠️  Failed to fetch playlist {pid}: {e}")

        return result

    @classmethod
    def get_playlist_tracks(cls, playlist_id: str) -> List[Dict]:
        """
        Return minimal rows for DB (plus added_at for playlist order):
          {id, name, artist, album, duration_ms, added_at}
        Skips podcast episodes and 'local file' placeholders that have no Spotify ID.
        """
        sp = cls.login()
        fields = (
            "items(added_at,track(id,type,name,artists(name),album(name,release_date,images),duration_ms,external_ids)),"
            "next"
        )
        results = cls._retry(sp.playlist_tracks, playlist_id, fields=fields, limit=100)
        tracks: List[Dict] = []
        while results:
            for item in results["items"]:
                tr = item.get("track")
                if not tr:
                    continue
                # Skip non-tracks (episodes) or items without a valid Spotify ID
                if tr.get("type") != "track" or not tr.get("id"):
                    continue
                tracks.append({
                    "id": tr["id"],
                    "name": tr["name"],
                    "artist": ", ".join(a["name"] for a in tr["artists"]),
                    "album": tr["album"]["name"],
                    "cover_url": (sorted((tr["album"].get("images") or []), key=lambda x: (x.get("width") or 0), reverse=True)[0].get("url")
                                   if (tr["album"].get("images") or []) else None),
                    "duration_ms": tr.get("duration_ms"),
                    "release_date": tr["album"].get("release_date"),
                    "isrc": tr.get("external_ids", {}).get("isrc"),
                    "added_at": item.get("added_at"),
                })
            results = cls._retry(sp.next, results) if results.get("next") else None
            if results and not results.get("items"):
                break
        return tracks

    # ------------------------
    # Metadata enrichment for tagging
    # ------------------------
    @classmethod
    def _get_artist_genres(cls, artist_id: str) -> List[str]:
        if artist_id in cls.cache.artist_genres:
            return cls.cache.artist_genres[artist_id]
        sp = cls.login()
        art = cls._retry(sp.artist, artist_id)
        genres = art.get("genres") or []
        cls.cache.artist_genres[artist_id] = genres
        return genres

    @staticmethod
    def _build_meta(tr: Dict, genres: List[str]) -> Dict:
        album = tr["album"]

        images = album.get("images") or []
        cover_url = None
        if images:
            cover_url = sorted(
                images, key=lambda x: (x.get("width") or 0), reverse=True
            )[0]["url"]

        primary_artist = tr["artists"][0]
        album_artist = primary_artist["name"]
        if album.get("artists"):
            album_artist = album["artists"][0]["name"]

        return {
            "id": tr["id"],
            "title": tr["name"],
            "artists": [a["name"] for a in tr["artists"]],
            "artist": ", ".join(a["name"] for a in tr["artists"]),
            "album": album["name"],
            "album_artist": album_artist,
            "track_number": tr.get("track_number"),
            "disc_number": tr.get("disc_number"),
            "duration_ms": tr.get("duration_ms"),
            "release_date": album.get("release_date"),
            "cover_url": cover_url,
            "genres": genres,
        }

    @classmethod
    def get_track_details(cls, track_id: str) -> Dict:
        """
        Rich metadata for tagging:
          title, artist(s), album, album_artist, track/disc numbers,
          duration, release_date, cover_url, genres (from primary artist)
        """
        sp = cls.login()
        tr = cls._retry(sp.track, track_id)
        primary_artist = tr["artists"][0]
        genres = cls._get_artist_genres(primary_artist["id"])
        return cls._build_meta(tr, genres)

    @classmethod
    def get_tracks_details(cls, track_ids: List[str]) -> List[Dict]:
        """Batch variant of ``get_track_details``."""
        if not track_ids:
            return []

        # Deduplicate while preserving order to avoid redundant lookups
        track_ids = list(dict.fromkeys(track_ids))

        sp = cls.login()

        # Fetch track objects in batches of <=50
        tracks: List[Dict] = []
        for i in range(0, len(track_ids), 50):
            chunk = track_ids[i : i + 50]
            res = cls._retry(sp.tracks, chunk)
            for tr in res.get("tracks", []):
                if tr:
                    tracks.append(tr)

        # Collect primary artist IDs that are not yet cached
        artist_ids: List[str] = []
        for tr in tracks:
            a_id = tr["artists"][0]["id"]
            if a_id not in cls.cache.artist_genres:
                artist_ids.append(a_id)

        # Deduplicate while preserving order
        seen = set()
        unique_artist_ids = []
        for a in artist_ids:
            if a not in seen:
                seen.add(a)
                unique_artist_ids.append(a)

        # Fetch artist genres in batches of <=50 and update cache
        for i in range(0, len(unique_artist_ids), 50):
            chunk = unique_artist_ids[i : i + 50]
            arts = cls._retry(sp.artists, chunk)
            for art in arts.get("artists", []):
                cls.cache.artist_genres[art["id"]] = art.get("genres") or []

        # Build metadata dicts matching ``get_track_details`` structure
        metas: List[Dict] = []
        for tr in tracks:
            primary_artist = tr["artists"][0]
            genres = cls.cache.artist_genres.get(primary_artist["id"], [])
            metas.append(cls._build_meta(tr, genres))

        return metas


class _KeychainCacheHandler(CacheHandler):
    def __init__(self, *, service: str, account: str):
        self._service = service
        self._account = account

    def get_cached_token(self):
        try:
            p = subprocess.run(
                [
                    "/usr/bin/security",
                    "find-generic-password",
                    "-a",
                    self._account,
                    "-s",
                    self._service,
                    "-w",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if p.returncode != 0:
                return None
            raw = (p.stdout or "").strip()
            if not raw:
                return None
            token_info = json.loads(raw)
            return token_info if isinstance(token_info, dict) else None
        except Exception:
            return None

    def save_token_to_cache(self, token_info):
        try:
            raw = json.dumps(token_info or {}, separators=(",", ":"))
            subprocess.run(
                [
                    "/usr/bin/security",
                    "add-generic-password",
                    "-a",
                    self._account,
                    "-s",
                    self._service,
                    "-w",
                    raw,
                    "-U",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:
            pass

    def delete(self) -> None:
        try:
            subprocess.run(
                [
                    "/usr/bin/security",
                    "delete-generic-password",
                    "-a",
                    self._account,
                    "-s",
                    self._service,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:
            pass


class _LayeredCacheHandler(CacheHandler):
    """Use keychain when possible but always keep a file fallback."""

    def __init__(self, *, primary: CacheHandler, fallback: CacheHandler):
        self._primary = primary
        self._fallback = fallback

    @staticmethod
    def _has_token_payload(token_info) -> bool:
        return bool(
            isinstance(token_info, dict)
            and (token_info.get("refresh_token") or token_info.get("access_token"))
        )

    def get_cached_token(self):
        token = None
        try:
            token = self._primary.get_cached_token()
        except Exception:
            token = None
        if self._has_token_payload(token):
            return token

        try:
            token = self._fallback.get_cached_token()
        except Exception:
            token = None
        return token if self._has_token_payload(token) else None

    def save_token_to_cache(self, token_info):
        try:
            self._primary.save_token_to_cache(token_info)
        except Exception:
            pass
        try:
            self._fallback.save_token_to_cache(token_info)
        except Exception:
            pass

    def delete(self) -> None:
        try:
            if hasattr(self._primary, "delete"):
                self._primary.delete()  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            if hasattr(self._fallback, "delete"):
                self._fallback.delete()  # type: ignore[attr-defined]
        except Exception:
            pass


def _make_file_cache_handler() -> CacheHandler:
    try:
        from pathlib import Path

        cache_dir = SpotifyClient._default_app_data_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / "spotify_token.json"
        return CacheFileHandler(cache_path=str(cache_path))
    except Exception:
        return CacheFileHandler(cache_path=".spotify_token.json")


def _make_cache_handler() -> CacheHandler:
    # Prefer macOS Keychain, but always keep a local cache fallback.
    file_handler = _make_file_cache_handler()

    try:
        if os.name == "posix" and os.path.exists("/usr/bin/security"):
            keychain_handler = _KeychainCacheHandler(
                service=SpotifyClient._KC_SERVICE,
                account=SpotifyClient._KC_ACCOUNT,
            )
            return _LayeredCacheHandler(primary=keychain_handler, fallback=file_handler)
    except Exception:
        pass

    return file_handler
