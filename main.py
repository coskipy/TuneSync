from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# load .env into environment
load_dotenv()

sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    scope="playlist-read-private"
))

# sanity check: print your username and playlists
me = sp.current_user()
print("✅ Logged in as:", me["display_name"])

