from glob import glob
import json
from pathlib import Path
import re
from flask import Flask, render_template, request, jsonify, redirect, session
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
from dotenv import load_dotenv

load_dotenv()  # Load from .env file

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") # Set a secret key for session management
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
    client_id=os.getenv("SPOTIFY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
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

    try:
        user_info = sp_client.me()
        return jsonify(user_info)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

"""OAuth callback route"""
@app.route('/callback')
def callback():
    code = request.args.get("code")
    
    if not code:
        return jsonify({"error": "Missing authorization code"}), 400

    try:
        token_info = sp_oauth.get_access_token(code)
    except Exception as e:
        return jsonify({"error": "Failed to exchange code for token", "message": str(e)}), 400

    if not token_info:
        return jsonify({"error": "Failed to authenticate"}), 400
    
    # Store token in session instead of global variable
    session["spotify_access_token"] = token_info["access_token"]
    session["refresh_token"] = token_info["refresh_token"]
    
    # Fetch user info
    sp_client = get_spotify_client()
    user_info = sp_client.me()
    
    # Redirect with username
    return redirect(f"/?user_name={user_info['display_name']}")

"""Get user playlists from Spotify"""
@app.route('/get-user-playlists', methods=['GET'])
def get_user_playlists():
    # Ensure user is authenticated
    sp_client = get_spotify_client()

    if not sp_client:
        return jsonify({"error": "User not authenticated. Please log in."}), 401
    
    offset = 0
    user_playlists = []
    # return sp_client.current_user_playlists()
    while True:
        # Fetch the user's playlists
        batch = sp_client.current_user_playlists(limit=50, offset=offset).get('items', [])
        if not batch:
            break
        user_playlists.extend(batch)
        offset += 50

    return user_playlists
    
    # Prepare the playlists in a JSON-friendly format   
    playlist_data = []
    for playlist in user_playlists['items']:
        playlist_data.append({
            'name': playlist['name'],
            'url': playlist['external_urls']['spotify']
        })
    



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

        num_synced = 0

        for path in paths:
            result, responseCode = sync_directory(path)
            num_synced += 1

        return jsonify({"status": "success", "message": f"Successfully synced {num_synced} playlists."}), 200

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
    get_spotify_client() # Refresh token if needed

    if (os.path.isdir(path) and (len(urls) > 0)):

        # Convert URLs to a single string for the command
        formatted_path = f"{path}/{{artist}} - {{title}}.{{output-ext}}"

        command = ["spotdl", "download"] + urls + ["--output", formatted_path]

        if spotify_access_token:
            command += ["--user-auth", "--auth-token", spotify_access_token]

        print(command, flush=True)
        try: 
            current_process = subprocess.Popen(
                command,
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
        
        return jsonify({"status": "success", "message": f"Download started. stdout: {stdout} stderr: {stderr}"}), 200
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

if __name__ == '__main__':
    app.run(debug=True)