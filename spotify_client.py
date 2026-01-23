# spotify_client.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional
from time import sleep
import threading
import json
import os
import subprocess

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

    _KC_SERVICE = "LightSync Spotify Token"
    _KC_ACCOUNT = "default"

    @classmethod
    def login(cls, scope: str = "playlist-read-private", silent: bool = False) -> spotipy.Spotify:
        load_dotenv()
        client_id = (os.getenv("SPOTIPY_CLIENT_ID") or "").strip() or None
        redirect_uri = (os.getenv("SPOTIPY_REDIRECT_URI") or "").strip() or None

        # In the GUI, we want to avoid unexpectedly popping a browser during background actions.
        open_browser = not bool(silent)

        if cls.sp is None or cls._auth_open_browser != open_browser or cls._auth_scope != scope:
            cache_handler = _make_cache_handler()
            auth = SpotifyPKCE(
                client_id=client_id,
                redirect_uri=redirect_uri,
                scope=scope,
                open_browser=open_browser,
                cache_handler=cache_handler,
            )
            cls.sp = spotipy.Spotify(auth_manager=auth)
            cls._auth_open_browser = open_browser
            cls._auth_scope = scope

        if not silent:
            me = cls.sp.current_user()
            print("✅ Logged in as:", me.get("display_name") or me.get("id"), flush=True)

        return cls.sp

    @classmethod
    def has_cached_token(cls) -> bool:
        try:
            load_dotenv()
            handler = _make_cache_handler()
            token = handler.get_cached_token()
            return bool(token and isinstance(token, dict))
        except Exception:
            return False

    @classmethod
    def logout(cls) -> None:
        try:
            handler = _make_cache_handler()
            if hasattr(handler, "delete"):
                handler.delete()  # type: ignore[attr-defined]
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
            try:
                # Rate limit protection: serialize requests
                with cls._request_lock:
                    sleep(cls._min_request_delay)
                    return fn(*args, **kwargs)
            except SpotifyException as e:
                if (e.http_status in (429, 500, 502, 503, 504)) and i < tries - 1:
                    backoff = delay * (2 ** i)
                    print(f"  ⏱️  Rate limited (HTTP {e.http_status}), waiting {backoff}s...")
                    sleep(backoff)
                    continue
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


def _make_cache_handler() -> CacheHandler:
    # Prefer macOS Keychain (no extra Python deps). Fallback to a local cache file.
    try:
        if os.name == "posix" and os.path.exists("/usr/bin/security"):
            return _KeychainCacheHandler(service=SpotifyClient._KC_SERVICE, account=SpotifyClient._KC_ACCOUNT)
    except Exception:
        pass

    try:
        from pathlib import Path

        cache_dir = Path.home() / "Library" / "Application Support" / "LightSync"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / "spotify_token.json"
        return CacheFileHandler(cache_path=str(cache_path))
    except Exception:
        return CacheFileHandler(cache_path=".spotify_token.json")
