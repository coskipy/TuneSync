# spotify_client.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional
from time import sleep

from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException


@dataclass
class _Cache:
    artist_genres: Dict[str, List[str]]


class SpotifyClient:
    sp: Optional[spotipy.Spotify] = None  # class-wide client
    cache = _Cache(artist_genres={})

    @classmethod
    def login(cls, scope: str = "playlist-read-private") -> spotipy.Spotify:
        if cls.sp is None:
            load_dotenv()
            cls.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(scope=scope))
            me = cls.sp.current_user()
            print("✅ Logged in as:", me.get("display_name") or me.get("id"))
        return cls.sp

    # ------------------------
    # Internal helpers
    # ------------------------
    @staticmethod
    def _retry(fn, *args, **kwargs):
        """Tiny retry helper for occasional rate limits and transient 5xx."""
        tries = 3
        delay = 1.5
        for i in range(tries):
            try:
                return fn(*args, **kwargs)
            except SpotifyException as e:
                if (e.http_status in (429, 500, 502, 503, 504)) and i < tries - 1:
                    sleep(delay)
                    delay *= 2
                    continue
                raise

    # ------------------------
    # Playlists
    # ------------------------
    @classmethod
    def get_my_playlists(cls) -> List[Dict]:
        sp = cls.login()
        results = cls._retry(sp.current_user_playlists, limit=50)
        playlists: List[Dict] = []
        while results:
            for p in results["items"]:
                playlists.append({
                    "id": p["id"],
                    "name": p["name"],
                    "snapshot_id": p.get("snapshot_id"),
                })
            results = cls._retry(sp.next, results) if results.get("next") else None
        return playlists

    @classmethod
    def get_playlist_metadata(cls, playlist_id: str) -> Dict:
        sp = cls.login()
        p = cls._retry(sp.playlist, playlist_id, fields="id,name,snapshot_id")
        return {"id": p["id"], "name": p["name"], "snapshot_id": p.get("snapshot_id")}

    @classmethod
    def get_playlist_tracks(cls, playlist_id: str) -> List[Dict]:
        """
        Return minimal rows for DB (plus added_at for playlist order):
          {id, name, artist, album, duration_ms, added_at}
        Skips podcast episodes and 'local file' placeholders that have no Spotify ID.
        """
        sp = cls.login()
        fields = (
            "items(added_at,track(id,type,name,artists(name),album(name),duration_ms)),"
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
                    "duration_ms": tr.get("duration_ms"),
                    "added_at": item.get("added_at"),
                })
            results = cls._retry(sp.next, results) if results.get("next") else None
            if results and not results.get("items"):  # defensive
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
        """
        Batch variant of ``get_track_details``.

        Retrieves metadata for multiple tracks, fetching primary artist genres in
        batches and updating the in-memory cache. Returns a list of metadata
        dictionaries with the same structure as ``get_track_details``.
        """
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
