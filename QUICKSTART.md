# 🚀 Quick Start Guide

Get TuneSync running in 5 minutes!

## Step 1: Install Prerequisites

```bash
# macOS
brew install ffmpeg python@3

# Linux (Ubuntu/Debian)
sudo apt-get update
sudo apt-get install ffmpeg python3 python3-pip python3-venv
```

## Step 2: Clone & Setup

```bash
# Clone the repo
git clone https://github.com/coskipy/TuneSync.git
cd TuneSync

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Step 3: Configure Spotify API

1. Go to https://developer.spotify.com/dashboard
2. Click "Create App"
3. Fill in:
    - App Name: `TuneSync`
    - App Description: `Personal DJ library sync`
    - Redirect URI: `http://127.0.0.1:5000/callback` ⚠️ IMPORTANT
4. Save and copy your **Client ID** and **Client Secret**

## Step 4: Create Configuration

```bash
# Copy the example
cp .env.example .env

# Edit the .env file with your details
nano .env  # or use your favorite editor
```

Fill in:

```bash
SPOTIPY_CLIENT_ID=paste_your_client_id_here
SPOTIPY_CLIENT_SECRET=paste_your_secret_here
SPOTIPY_REDIRECT_URI=http://127.0.0.1:5000/callback

DOWNLOAD_ROOT=/Users/yourname/Music/DJ
REKORDBOX_XML_PATH=/Users/yourname/Music/DJ/rekordbox.xml
```

## Step 5: Run!

```bash
python -m tunesync_app
```

On first run:

- Your browser will open
- Click "Agree" to authorize
- TuneSync will start syncing!

Inside the app:

- Use **Add Playlists** to select what to sync
- Use the sync/export actions to run the pipeline

## 🎉 That's It!

Your tracks will be downloaded to the folder you specified, automatically tagged with metadata and album art, and a `rekordbox.xml` file will be created for easy import into Rekordbox.

---

## Common Issues

**"ffmpeg not found"**

```bash
# Make sure ffmpeg is in your PATH
which ffmpeg  # Should show a path
```

**"Invalid client credentials"**

- Double-check your Client ID and Secret in `.env`
- Make sure there are no extra spaces

**"Redirect URI mismatch"**

- Verify the redirect URI in your Spotify app settings is EXACTLY: `http://127.0.0.1:5000/callback`

**Python version too old**

```bash
python3 --version  # Should be 3.9 or higher
```

---

Need more help? Check the full [README.md](README.md)
