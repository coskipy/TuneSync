from glob import glob
import json
from pathlib import Path
import re
from flask import Flask, render_template, request, jsonify, redirect
from flask_cors import CORS
import subprocess
import logging
import os
import signal
import spotipy
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth
import time
from functools import lru_cache


app = Flask(__name__)
CORS(app)  # Allow all origins
syncedDirsJson = "syncedDirs.json"

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
spotify_access_token = ""

# Global variable to store the current process
current_process = None

# Set up the Spotify OAuth object
sp_oauth = SpotifyOAuth(
    client_id="cde55a79f546483bad4e30ec92c7c45b",
    client_secret="b31d3b6144334d5088c1b371e9367ef2",
    redirect_uri="http://127.0.0.1:5000/callback",  # Must match Spotify dev dashboard
    scope="user-library-read playlist-read-private",
    cache_path="token_cache.json"  # Store tokens here
)

sp = spotipy.Spotify(auth_manager=sp_oauth)

def get_spotify_client():
    token_info = sp_oauth.get_cached_token()

    if not token_info:
        print("⚠️ No cached token found. User needs to log in.")
        return None  # Return None if no token is available

    if sp_oauth.is_token_expired(token_info):
        print("🔄 Access token expired. Refreshing...")
        token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])

    return spotipy.Spotify(auth=token_info['access_token'])


@app.route('/')
def index():
    # Retrieve the user's name and access token from the URL query parameters
    user_name = request.args.get('user_name', 'Guest')  # Default to 'Guest' if no name is passed
    access_token = request.args.get('access_token', None)

    # Pass these values to your HTML template
    return render_template('index.html', user_name=user_name, access_token=access_token)


# OAuth login route
@app.route('/login')
def login():
    return redirect(sp_oauth.get_authorize_url())

@app.route('/get-spotify-user')
def get_current_user():
    sp_client = get_spotify_client()
    
    if not sp_client:
        return jsonify({"error": "User not authenticated. Please log in."}), 401

    user_info = sp_client.me()
    return jsonify(user_info)

"""OAuth callback route"""
@app.route('/callback')
def callback():
    code = request.args.get("code")
    
    try:
        token_info = sp_oauth.get_access_token(code)
    except Exception as e:
        return jsonify({"error": "Failed to exchange code for token", "message": str(e)}), 400

    if not token_info:
        return jsonify({"error": "Failed to authenticate"}), 400
    
    spotify_access_token = token_info["access_token"]
    
    # Use the access token to get the user's profile
    sp = spotipy.Spotify(auth=spotify_access_token)
    user_info = sp.current_user()  # Fetch the current user's info

    user_name = user_info['display_name']  # Get the user's name

    # Redirect to the home page with the user's name and access token as query parameters
    return redirect(f"/?user_name={user_name}&access_token={spotify_access_token}")


"""Function to add a path string to the JSON file"""
def add_path_to_json(file_name, new_path):
    playlist_paths = []
    
    # Check if the file exists
    if Path(file_name).exists():
        # Attempt to load JSON content
        try:
            with open(file_name, "r") as f:
                if f.read().strip():  # Check if the file is not empty
                    f.seek(0)  # Go back to the beginning of the file
                    playlist_paths = json.load(f)
        except json.JSONDecodeError:
            # Handle cases where the file contains invalid JSON
            print(f"Warning: {file_name} contains invalid JSON. Reinitializing.")
    
    # Add the new path to the list if it's not already there
    if new_path not in playlist_paths:
        playlist_paths.append(new_path)
    
    # Write the updated list back to the file
    with open(file_name, "w") as f:
        json.dump(playlist_paths, f, indent=4)


"""Download a playlist, song or album from Spotify. Metadata for syncing is saved alongside file"""
@app.route('/download', methods=['POST'])
def download():
    global current_process
    data = request.json

    url = data.get('url')
    path = data.get('path')

    # Create cache file to store location of all synced playlists
    if not os.path.exists(syncedDirsJson):
        with open(syncedDirsJson, "w") as file:
            pass  # The file is now created but empty

    # Save extra metadata if its a playlist; for syncing
    if ("/playlist/" in url):
        add_path_to_json(syncedDirsJson, path) # Add target dir to sync cache
        playlist_metadata = get_playlist_metadata(url)
        
        # Create metadata file within the target directory for use syncing the playlist
        with open(os.path.join(path, "metadata.json"), "w") as temp_file:
            json.dump(playlist_metadata, temp_file, indent=4)

    result = spotdl_download([url], path)

    return result


"""Stop the current download process"""
@app.route('/stop-download', methods=['POST'])
def stop_download():
    global current_process
    if current_process:
        current_process.send_signal(signal.SIGINT)  # Send Ctrl+C signal
        current_process = None
        return jsonify({"status": "success", "message": "Download stopped"})
    else:
        return jsonify({"status": "error", "message": "No download process running"}), 400


"""Sync all downloaded playlists using the metadata file in the directory"""
@app.route('/sync-all', methods=['POST'])
def sync_all():
    # If no synced directories exist
    if not os.path.exists(syncedDirsJson):
        return jsonify({"status": "error", "message": "No directories to sync."}), 500
    
    # Get a list of paths pointing to existing playlist directories to sync
    with open(syncedDirsJson, "r") as f:
        playlist_paths = json.load(f)
    num_synced = 0

    for path in playlist_paths:
        response_json, status_code = sync_directory(path)
        response_json = response_json.get_json()  # Convert Response to dict
        if response_json.get('status', '') == 'success':
            num_synced += 1
        
    return jsonify({"status": "success", "message": f"Successfully synced {num_synced} playlists. {response_json}"}), 200


@app.route('/sync-selected', methods=['POST'])
def sync_selected():
    global current_process
    try:
        # Extract the JSON body
        data = request.get_json()
        if not data or "paths" not in data:
            return jsonify({"message": "Invalid request, no paths provided"}), 400

        # Get the list of paths
        paths = data["paths"]

        number_synced = 0

        for path in paths:
            try:
                current_process = subprocess.Popen(
                    ["spotdl", "sync", os.path.cwjoin(path, "SyncData.spotdl"), "--output", path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                stdout, stderr = current_process.communicate()
                number_synced += 1

            except Exception as e:
                return jsonify({"status": "error", "message": str(e)}), 500

        return jsonify({"status": "success", "message": f"Successfully synced {number_synced} playlists."}), 200

    except Exception as e:
        return jsonify({"message": f"An error occurred: {str(e)}"}), 500

def sync_directory(path):
    if os.path.isdir(path):
            try:
                # Get downloaded metadata
                with open(os.path.join(path, 'metadata.json'), 'r') as downloaded_metadata:
                    downloaded_metadata = json.load(downloaded_metadata)

                # Create map to delete songs based on ID. Map: song_id -> "{artist} - {title}"
                metadata_map = {
                    song["song_id"]: f"{song['artist']} - {song['name']}"
                    for song in downloaded_metadata.get('songs', [])
                }
                
                # Get latest metadata
                playlist_link = downloaded_metadata.get('query', {})[0]
                playlist_metadata = get_playlist_metadata(playlist_link)

                # Get songs from metadata for comparison: song_id -> song object
                downloaded_songs = {song["song_id"]: song for song in downloaded_metadata["songs"]}
                playlist_songs = {song["song_id"]: song for song in playlist_metadata["songs"]}

                # Get songs to remove or add based on differences (list of song IDs)
                songs_to_remove = [song for song_id, song in downloaded_songs.items() if song_id not in playlist_songs]
                songs_to_add = [song for song_id, song in playlist_songs.items() if song_id not in downloaded_songs]

                # Delete excess songs
                for song in songs_to_remove:
                    song_name = metadata_map[song.get("song_id", "")]
                    matching_files = glob(f"{path}/{song_name}.*")  # Find any file with this name
                    for file in matching_files:
                        os.remove(file)  # Remove each found file

                # Download new songs
                if songs_to_add:
                    download_urls = [f"https://open.spotify.com/track/{song['song_id']}" for song in songs_to_add] # Create 
                    spotdl_download(download_urls, path)

                # Update the metadata file with the latest metadata
                with open(os.path.join(path, "metadata.json"), "w") as old_metadata:
                    json.dump(playlist_metadata, old_metadata, indent=4)

            except SpotifyException as se:
                return jsonify({"status": "error", "message": "Spotify error syncing directory"}), 500
            except Exception as e:
                return jsonify({"status": "error", "message": f"Error syncing directory: {e}"}), 500        
    else:
        # Remove the path from the playlist_paths list and update the JSON file
        playlist_paths = [p for p in playlist_paths if p != path]

        # Update the JSON file to reflect the removal of the invalid path
        with open(syncedDirsJson, "w") as f:
            json.dump(playlist_paths, f, indent=4)

        return jsonify({"status": "error", "message": f"Directory {path} does not exist. Removed from sync list."}), 400

    return jsonify({"status": "success", 
                    "message": f"Playlist synced successfully"}), 200
    


"""Download a list of songs from Spotify using spotdl"""
def spotdl_download(urls: list, path):
    global current_process # Use global so download can be cancelled by another function
    if (os.path.isdir(path) and (len(urls) > 0)):

        # Convert URLs to a single string for the command
        query_string = " ".join([f"'{url}'" for url in urls])
        formatted_path = f"'{path}/{{artist}} - {{title}}.{{output-ext}}'"

        command = ["spotdl", "download", query_string, "--output", formatted_path]

        if spotify_access_token != "":
            command.append("--user-auth")
            command.append("--auth-token")
            command.append(spotify_access_token)

        command = " ".join(command)
        try: 
            current_process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
                )
            
            # Wait for the process to complete and capture the output
            stdout, stderr = current_process.communicate()
            
        except SpotifyException as se:
            return jsonify({'error': se, 'message': 'Spotify error while downloading'}), 500
        except Exception as e:
            return jsonify({'error': e, 'message': 'Error while downloading'}), 500
        
        return jsonify({"status": "success", "message": "Download started", "stdout": stdout, "stderr": stderr}), 200
    else:
        return jsonify({"status": "error", "message": f"Invalid path or missing urls: {path, urls}", "path": path, "urls": urls}), 400


"""Get metadata from Spotify playlists, MORE TO COME"""
@app.route('/get-metadata', methods=['POST'])
def get_metadata():
    global current_process
    data = request.json
    url = data.get('url')
    path = data.get('path')

    if ("/playlist/" in url):
        return get_playlist_metadata(url)


"""Get metadata for every song in a Spotify playlist."""
def get_playlist_metadata(url):
    # Extract playlist ID from URL
    playlist_id = url.split('/')[-1].split('?')[0]

    # Variables for big playlists and song position
    offset = 0
    batch_size = 100
    list_position = 0

    all_tracks = []

    try:
        playlist_info = sp.playlist(playlist_id) # API CALL
    except SpotifyException as e:
        return jsonify({'error': e, 'messaage': 'Error getting playlist metadata from Spotify'})

    while True:
        try:
            playlist_items = sp.playlist_tracks(playlist_id, offset=offset, limit=batch_size).get('items', {}) # API CALL

            if not playlist_items:
                break

            for item in playlist_items:
                track = item.get('track', {})
                album = track.get('album', {})

                artists = []
                for artist in track.get('artists', {}):
                    artists.append(artist['name'])

                track_info = {
                    "name": track.get('name', ''),
                    "artists": artists,
                    "artist": artists[0],
                    "disc_number": track.get('disc_number', 1),
                    "disc_count": track.get('disc_number', 1), # Just reusing disc number
                    "album_name": album.get('name', ''),
                    "album_artist": album.get('artists', {})[0].get('name', ''),
                    "duration": track.get('duration_ms', 0)/1000,
                    "year": album.get('release_date', '9999')[:4],
                    "date": album.get('release_date', ''),
                    "track_number": track['track_number'],
                    "tracks_count": album.get('total_tracks', 0),
                    "song_id": track.get('id', ''),
                    "explicit": track.get('explicit', False),
                    "url": f"https://open.spotify.com/track/{track.get('id', '')}",
                    "isrc": track.get('external_ids', {}).get('isrc', ''),
                    "cover_url": album.get('images', {})[0].get('url', ''),
                    "popularity": track.get('popularity', ''),
                    "album_id": album.get('id', ''),
                    "list_name": playlist_info.get('name', ''),
                    "list_url": f"https://open.spotify.com/playlist/{playlist_info.get('id', '')}",
                    "list_position": list_position,
                    "list_length": playlist_info.get('tracks', {}).get('total', -1),
                    "artist_id": track.get('artists', {})[0].get('id', 0),
                    "album_type": album.get('album_type', '')
                }
                all_tracks.append(track_info)
                list_position += 1
            offset += batch_size

        except SpotifyException as se:
            return jsonify({'error': se, 'message': 'Error getting song metadata from Spotify'})
        except Exception as e:
            return jsonify({'error': e, 'message': 'Error formatting metadata'})
    return {
        "type": "sync",
        "query": [url],
        "songs": all_tracks
    }

# def get_playlist_metadata(url):
#     # Extract playlist ID from URL
#     playlist_id = url.split('/')[-1].split('?')[0]
    
#     # Fetch playlist metadata to get name and total tracks
#     playlist = sp.playlist(playlist_id)
#     playlist_name = playlist['name']
#     total_tracks = playlist['tracks']['total']
    
#     offset = 0
#     limit = 25  # Reduced batch size
#     all_tracks = []
    
#     # Pre-fetch all artist IDs in the playlist first
#     all_artist_ids = set()
    
#     # First pass: Collect all artist IDs
#     while True:
#         results = sp.playlist_tracks(playlist_id, offset=offset, limit=limit)
#         tracks = results['items']
        
#         if not tracks:
#             break
            
#         for track_item in tracks:
#             if track_item['track']:
#                 all_artist_ids.update(artist['id'] for artist in track_item['track']['artists'])
        
#         offset += limit
#         if offset >= results['total']:
#             break
    
#     # Batch fetch all artists (50 per request - Spotify's max)
#     artists_cache = {}
#     artist_id_list = list(all_artist_ids)
#     for i in range(0, len(artist_id_list), 50):
#         batch = artist_id_list[i:i+50]
#         artists = sp.artists(batch)['artists']
#         for artist in artists:
#             if artist:  # Skip invalid responses
#                 artists_cache[artist['id']] = artist
#         time.sleep(0.5)  # Add delay between batches
    
#     # Second pass: Build track data using cache
#     offset = 0
#     while True:
#         results = sp.playlist_tracks(playlist_id, offset=offset, limit=limit)
#         tracks = results['items']
        
#         if not tracks:
#             break
            
#         batch = []
#         for idx, track_item in enumerate(tracks):
#             track_data = track_item['track']
#             if not track_data:
#                 continue
                
#             album_data = track_data['album']
            
#             # Get genres from cache
#             artists_genres = []
#             for artist in track_data['artists']:
#                 artist_info = artists_cache.get(artist['id'], {})
#                 artists_genres.extend(artist_info.get('genres', []))
            
#             # Build track info (keep your existing structure)
#             track_info = {
#                 "name": track_data.get('name'),
#                 "artists": [artist.get('name') for artist in track_data.get('artists', [])],
#                 "artist": track_data['artists'][0]['name'] if track_data.get('artists') else '',
#                 "genres": artists_genres,
#                 "disc_number": track_data.get('disc_number', 0),
#                 "album_name": album_data.get('name'),
#                 "album_artist": album_data['artists'][0]['name'] if album_data.get('artists') else '',
#                 "duration": track_data.get('duration_ms', 0) // 1000,
#                 "year": album_data.get('release_date', '')[:4] if album_data.get('release_date') else '',
#                 "date": album_data.get('release_date', ''),
#                 "track_number": track_data.get('track_number', 0),
#                 "tracks_count": album_data.get('total_tracks', 0),
#                 "song_id": track_data.get('id'),
#                 "explicit": track_data.get('explicit', False),
#                 "publisher": album_data.get('label', ''),
#                 "url": track_data['external_urls'].get('spotify', ''),
#                 "isrc": track_data.get('external_ids', {}).get('isrc', ''),
#                 "cover_url": album_data['images'][0]['url'] if album_data.get('images') else '',
#                 "copyright_text": f"{album_data.get('release_date', '')[:4]} {album_data.get('label', '')}".strip(),
#                 "download_url": None,
#                 "lyrics": None,
#                 "popularity": track_data.get('popularity', 0),
#                 "album_id": album_data.get('id'),
#                 "list_name": playlist_name,
#                 "list_url": f"https://open.spotify.com/playlist/{playlist_id}",
#                 "list_position": offset + idx,  # Correct playlist position
#                 "list_length": total_tracks,
#                 "artist_id": track_data['artists'][0]['id'] if track_data.get('artists') else '',
#                 "album_type": album_data.get('album_type', '')
#             }
#             batch.append(track_info)
        
#         all_tracks.extend(batch)
#         offset += limit
#         if offset >= results['total']:
#             break
        
#         time.sleep(1)  # Add delay between track batches
    
#     return all_tracks

# def batch_fetch_artists(artist_ids):
#     artists_cache = {}
#     for i in range(0, len(artist_ids), 50):
#         batch = artist_ids[i:i+50]
        
#         # Get rate limit status
#         remaining = int(sp.last_response.headers.get('X-RateLimit-Remaining', 30)) if i > 0 else 30
        
#         if remaining < 5:
#             reset = int(sp.last_response.headers.get('X-RateLimit-Reset', 30))
#             time.sleep(reset + 2)
        
#         artists = sp.artists(batch)['artists']
        
#         # Store in cache
#         for artist in artists:
#             if artist:
#                 artists_cache[artist['id']] = artist
                
#         # Dynamic delay based on remaining capacity
#         time.sleep(max(0.5, (60 / remaining) if remaining > 0 else 5))
    
#     return artists_cache

# def get_playlist_metadata(url):
#     playlist_id = url.split('/')[-1].split('?')[0]
#     playlist = sp.playlist(playlist_id)
#     playlist_name = playlist['name']
#     total_tracks = playlist['tracks']['total']
    
#     offset = 0
#     limit = 50  # Use maximum allowed batch size
#     all_tracks = []
#     all_artist_ids = set()

#     try:
#         results = sp.playlist_tracks


    
#     # Single pass through tracks
#     while True:
#         try:
#             results = sp.playlist_tracks(playlist_id, offset=offset, limit=limit)
#             remaining = int(results['headers'].get('X-RateLimit-Remaining', 30))
            
#             if remaining < 10:
#                 reset = int(results['headers'].get('X-RateLimit-Reset', 30))
#                 time.sleep(reset + 2)
                
#             tracks = results['items']
            
#             # Collect artist IDs and process tracks
#             current_batch_artist_ids = set()
#             for track_item in tracks:
#                 if not track_item['track']:
#                     continue
                
#                 track_data = track_item['track']
#                 artists = track_data['artists']
                
#                 # Collect artist IDs
#                 artist_ids = {a['id'] for a in artists}
#                 current_batch_artist_ids.update(artist_ids)
#                 all_artist_ids.update(artist_ids)
                
#                 # Store basic track info
#                 all_tracks.append({
#                     "name": track_data.get('name'),
#                     "artists": [a['name'] for a in artists],
#                     "artist": track_data['artists'][0]['name'] if track_data.get('artists') else '',
#                     "genres": artists_genres,
#                     "disc_number": track_data.get('disc_number', 0),
#                     "album_name": album_data.get('name'),
#                     "album_artist": album_data['artists'][0]['name'] if album_data.get('artists') else '',
#                     "duration": track_data.get('duration_ms', 0) // 1000,
#                     "year": album_data.get('release_date', '')[:4] if album_data.get('release_date') else '',
#                     "date": album_data.get('release_date', ''),
#                     "track_number": track_data.get('track_number', 0),
#                     "tracks_count": album_data.get('total_tracks', 0),
#                     "song_id": track_data.get('id'),
#                     "explicit": track_data.get('explicit', False),
#                     "publisher": album_data.get('label', ''),
#                     "url": track_data['external_urls'].get('spotify', ''),
#                     "isrc": track_data.get('external_ids', {}).get('isrc', ''),
#                     "cover_url": album_data['images'][0]['url'] if album_data.get('images') else '',
#                     "copyright_text": f"{album_data.get('release_date', '')[:4]} {album_data.get('label', '')}".strip(),
#                     "download_url": None,
#                     "lyrics": None,
#                     "popularity": track_data.get('popularity', 0),
#                     "album_id": album_data.get('id'),
#                     "list_name": playlist_name,
#                     "list_url": f"https://open.spotify.com/playlist/{playlist_id}",
#                     "list_position": offset + idx,  # Correct playlist position
#                     "list_length": total_tracks,
#                     "artist_id": track_data['artists'][0]['id'] if track_data.get('artists') else '',
#                     "album_type": album_data.get('album_type', '')
#                 })
            
#             # Fetch artists for this batch
#             artists_cache = batch_fetch_artists(list(current_batch_artist_ids))
            
#             # Add genres to tracks
#             for track in all_tracks[-len(tracks):]:
#                 track["genres"] = []
#                 for artist_id in [a['id'] for a in track['artists']]:
#                     artist = artists_cache.get(artist_id)
#                     if artist:
#                         track["genres"].extend(artist.get('genres', []))
            
#             offset += limit
#             if offset >= results['total']:
#                 break
                
#             # Dynamic delay between track batches
#             time.sleep(max(1, (60 / remaining) if remaining > 0 else 5))
            
#         except SpotifyException as e:
#             if e.http_status == 429:
#                 retry_after = int(e.headers.get("Retry-After", 30))
#                 time.sleep(retry_after)
#                 continue
#             raise
    
#     return all_tracks

if __name__ == '__main__':
    app.run(debug=True)