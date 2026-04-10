#!/usr/bin/env python3
"""
TuneSync Setup Script
Automates installation and configuration
"""

import os
import sys
import subprocess
import platform
from pathlib import Path


def print_header(text):
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}\n")


def print_step(num, text):
    print(f"\n[{num}/6] {text}")


def check_python_version():
    """Check if Python version is 3.9+"""
    print_step(1, "Checking Python version...")
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 9):
        print(f"❌ Python 3.9+ required. You have {version.major}.{version.minor}")
        print("   Please upgrade Python: https://www.python.org/downloads/")
        sys.exit(1)
    print(f"✅ Python {version.major}.{version.minor}.{version.micro}")


def check_ffmpeg():
    """Check if ffmpeg is installed"""
    print_step(2, "Checking ffmpeg installation...")
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5
        )
        if result.returncode == 0:
            print("✅ ffmpeg is installed")
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    print("❌ ffmpeg not found!")
    system = platform.system()
    if system == "Darwin":
        print("   Install with: brew install ffmpeg")
    elif system == "Linux":
        print("   Install with: sudo apt-get install ffmpeg")
    elif system == "Windows":
        print("   Download from: https://ffmpeg.org/download.html")
    
    response = input("\n   Continue anyway? (y/n): ").lower()
    if response != 'y':
        sys.exit(1)
    return False


def create_venv():
    """Create virtual environment"""
    print_step(3, "Setting up virtual environment...")
    venv_path = Path(".venv")
    
    if venv_path.exists():
        print("⚠️  Virtual environment already exists")
        response = input("   Recreate? This will reinstall dependencies (y/n): ").lower()
        if response == 'y':
            import shutil
            shutil.rmtree(venv_path)
        else:
            print("✅ Using existing virtual environment")
            return
    
    try:
        subprocess.run([sys.executable, "-m", "venv", ".venv"], check=True)
        print("✅ Virtual environment created")
    except subprocess.CalledProcessError:
        print("❌ Failed to create virtual environment")
        sys.exit(1)


def install_dependencies():
    """Install Python dependencies"""
    print_step(4, "Installing dependencies...")
    
    # Determine pip path based on OS
    if platform.system() == "Windows":
        pip_path = Path(".venv/Scripts/pip")
    else:
        pip_path = Path(".venv/bin/pip")
    
    if not pip_path.exists():
        print("❌ Virtual environment not properly created")
        sys.exit(1)
    
    try:
        print("   Installing packages... (this may take a minute)")
        subprocess.run(
            [str(pip_path), "install", "-r", "requirements.txt"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        print("✅ Dependencies installed")
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to install dependencies: {e}")
        sys.exit(1)


def create_env_file():
    """Create .env configuration file"""
    print_step(5, "Configuring environment...")
    
    env_path = Path(".env")
    if env_path.exists():
        print("⚠️  .env file already exists")
        response = input("   Overwrite? (y/n): ").lower()
        if response != 'y':
            print("✅ Keeping existing .env")
            return
    
    print("\n📝 Please provide the following information:\n")
    
    # Spotify credentials
    print("Spotify API Credentials")
    print("(Get from: https://developer.spotify.com/dashboard)")
    client_id = input("  Client ID: ").strip()
    client_secret = input("  Client Secret: ").strip()
    
    # Download path
    print("\n📁 Download Location")
    if platform.system() == "Windows":
        default_path = str(Path.home() / "Music" / "DJ")
    else:
        default_path = str(Path.home() / "Music" / "DJ")
    
    print(f"  Default: {default_path}")
    download_path = input(f"  Path (press Enter for default): ").strip()
    if not download_path:
        download_path = default_path
    
    download_path = Path(download_path).absolute()
    xml_path = download_path / "rekordbox.xml"
    
    # Create the directory if it doesn't exist
    download_path.mkdir(parents=True, exist_ok=True)
    
    # Write .env file
    env_content = f"""# Spotify API Credentials
SPOTIPY_CLIENT_ID={client_id}
SPOTIPY_CLIENT_SECRET={client_secret}
SPOTIPY_REDIRECT_URI=http://127.0.0.1:8765/callback

# Download Location
DOWNLOAD_ROOT={download_path}

# Rekordbox XML Export Path
REKORDBOX_XML_PATH={xml_path}
"""
    
    env_path.write_text(env_content)
    print(f"\n✅ Configuration saved to .env")
    print(f"   Music will be saved to: {download_path}")

def print_completion():
    """Print setup completion message"""
    print_step(6, "Setup complete! 🎉")
    
    print("\n" + "="*60)
    print("  🎵 TuneSync is ready to use!")
    print("="*60)
    
    print("\n📝 Next steps:")

    print("\n1. Activate the virtual environment:\n")
    
    if platform.system() == "Windows":
        print("     .venv\\Scripts\\activate")
    else:
        print("     source .venv/bin/activate")
    
    print("\n2. Run the TuneSync app:\n")
    print("     python -m tunesync_app")

    print("\n   Your browser will open for Spotify authorization on first run.")
    print("\n3. Add playlists from inside the app (Add Playlists).")
    
    print("\n💡 Tips:")
    print("  - Edit .env to change download location")
    print("  - Edit main.py to change MAX_WORKERS for faster downloads")
    print("  - Use the app to sync/export, or run 'python main.py' for CLI sync")
    print("\n")


def main():
    print_header("🎵 TuneSync Setup")
    
    try:
        check_python_version()
        check_ffmpeg()
        create_venv()
        install_dependencies()
        create_env_file()
        print_completion()
        
    except KeyboardInterrupt:
        print("\n\n❌ Setup cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Setup failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
