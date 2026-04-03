# 🎵 TuneSync

Automatically sync your Spotify playlists to local files for Rekordbox.

Downloads high-quality audio from SoundCloud/YouTube, tags with Spotify metadata, and exports to Rekordbox XML.

## ⚡ Quick Setup

### 1. Install ffmpeg

**macOS:**

```bash
brew install ffmpeg
```

**Linux:**

```bash
sudo apt-get install ffmpeg
```

**Windows:** Download from [ffmpeg.org](https://ffmpeg.org/download.html)

### 2. Get Spotify API Credentials

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Create a new app
3. Add redirect URI: `http://127.0.0.1:8765/callback`
4. Copy your **Client ID** and **Client Secret**

### 3. Run Setup Script

```bash
git clone https://github.com/coskipy/TuneSync.git
cd TuneSync
python3 setup.py
```

The setup script will:

- ✅ Check Python version
- ✅ Check ffmpeg installation
- ✅ Create virtual environment
- ✅ Install dependencies
- ✅ Create `.env` configuration file
- ✅ Prompt for your Spotify credentials and download path

### 4. Run TuneSync

```bash
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
python -m tunesync_app
```

Your browser will open for Spotify authorization on first run. That's it!

## 📖 What It Does

1. Syncs all your Spotify playlists
2. Searches for high-quality audio (SoundCloud first, YouTube fallback)
3. Downloads and tags with metadata + album art
4. Exports `rekordbox.xml` for easy DJ software import

## 🎯 Adding Playlists

Playlists are managed in the app and stored in the SQLite database (`TuneSync.sqlite3`).

1. Open the app: `python -m tunesync_app`
2. Go to **Add Playlists**
3. Select playlists and enable syncing

If you prefer CLI-only operation, you can still run `python main.py` to sync using whatever playlists are currently enabled in the DB.

## ⚙️ Configuration

Edit `.env` to change settings:

```bash
# Spotify API
SPOTIPY_CLIENT_ID=your_client_id
SPOTIPY_CLIENT_SECRET=your_client_secret
SPOTIPY_REDIRECT_URI=http://127.0.0.1:8765/callback

# Where to save music
DOWNLOAD_ROOT=/path/to/your/music/folder

# Where to save Rekordbox XML
REKORDBOX_XML_PATH=/path/to/your/music/folder/rekordbox.xml
```

Edit `main.py` for parallel downloads:

```python
MAX_WORKERS = 4  # Increase for faster downloads
```

## 🔄 Running Again

Just activate the virtual environment and run:

```bash
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
python -m tunesync_app
```

It only downloads new/missing tracks - existing files are skipped.

## 🎯 Features

- **Smart Search**: Finds official uploads using view counts and artist matching
- **Quality Filters**: Skips remixes, live versions, previews (<60s), radio edits
- **Multi-Source**: SoundCloud priority, YouTube fallback
- **Auto-Tagging**: Spotify metadata + album artwork
- **Fast**: Parallel downloads, smart playlist syncing

## 🛠️ Troubleshooting

**"ffmpeg not found"**

```bash
which ffmpeg  # Check if installed
brew install ffmpeg  # macOS
```

**Spotify authentication fails**

- Verify redirect URI is exactly: `http://127.0.0.1:8765/callback`
- Check credentials in `.env`

**Wrong song downloaded**

- The improved algorithm heavily favors official uploads with high view counts
- If wrong, the official version might not be available online

**Reset everything**

```bash
rm -rf /path/to/music/*.m4a
sqlite3 TuneSync.sqlite3 "DELETE FROM files; DELETE FROM tracks;"
python main.py
```

## 📝 Manual Setup (Without setup.py)

If you prefer manual installation:

```bash
# Clone repo
git clone https://github.com/coskipy/TuneSync.git
cd TuneSync

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy config template
cp .env.example .env

# Edit .env with your details
nano .env

# Run
python -m tunesync_app
```

---

**Made with 🎵 for DJs who love Spotify**
