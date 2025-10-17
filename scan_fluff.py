#!/usr/bin/env python3
"""
Scan existing downloaded files for "fluff" - problematic downloads:
- Songs over 15 minutes (likely DJ sets/podcasts)
- Songs under 30 seconds (likely previews)
- Songs with titles containing problematic keywords (remixes, live versions, etc.)
- Songs with significant duration mismatches from Spotify
"""

from pathlib import Path
from mutagen import File as MutagenFile
from db import get_conn
import sys


def get_audio_duration(filepath: Path) -> float | None:
    """Get duration of audio file in seconds."""
    try:
        audio = MutagenFile(filepath)
        if audio and hasattr(audio.info, 'length'):
            return audio.info.length
    except Exception:
        pass
    return None


def scan_for_fluff(music_dir: Path = None, show_all: bool = False):
    """
    Scan downloaded files for problematic content.
    
    Args:
        music_dir: Directory to scan (default: from DOWNLOAD_ROOT env var)
        show_all: Show all files, not just problematic ones
    """
    from dotenv import load_dotenv
    import os
    
    # Get DOWNLOAD_ROOT from environment
    if music_dir is None:
        load_dotenv()
        root = os.getenv("DOWNLOAD_ROOT")
        if not root:
            print("❌ DOWNLOAD_ROOT not set in .env and no --dir specified")
            return
        music_dir = Path(root)
    
    if not music_dir.exists():
        print(f"❌ Directory does not exist: {music_dir}")
        return
    
    conn = get_conn()
    
    # Get all files from database with their track info
    rows = conn.execute("""
        SELECT 
            f.file_path,
            t.id as track_id,
            t.artist,
            t.name as title,
            t.duration_ms
        FROM files f
        JOIN tracks t ON f.track_id = t.id
        ORDER BY f.file_path
    """).fetchall()
    
    if not rows:
        print("No files found in database.")
        return
    
    print(f"Scanning {len(rows)} files for problematic content...\n")
    print("=" * 100)
    
    categories = {
        'too_long': [],      # Over 15 minutes
        'too_short': [],     # Under 30 seconds
        'bad_keywords': [],  # Contains problematic keywords
        'duration_off': [],  # Duration significantly off from Spotify
        'missing': [],       # File doesn't exist
        'clean': [],         # Everything looks good
    }
    
    bad_keywords = [
        "radio edit", "radio version", "radio mix",
        "live", "live at", "live from", "concert",
        "karaoke", "instrumental", "backing track",
        "cover", "covered by",
        "dj set", "dj mix", "mix set", "continuous mix",
        "nightcore", "slowed", "reverb", "sped up",
        "acoustic", "unplugged",
        "parody", "spoof",
        "tutorial", "how to play", "lesson",
        "reaction", "review",
        "lyrics", "lyric video", "letra",
    ]
    
    for row in rows:
        filepath_rel = Path(row[0])
        filepath = music_dir / filepath_rel  # Reconstruct full path
        track_id = row[1]
        artist = row[2]
        title = row[3]
        spotify_duration_sec = row[4] / 1000.0 if row[4] else None
        
        label = f"{artist} - {title}"
        
        # Check if file exists
        if not filepath.exists():
            categories['missing'].append({
                'path': filepath,
                'label': label,
                'track_id': track_id,
                'reason': 'File not found'
            })
            continue
        
        # Get actual duration
        actual_duration = get_audio_duration(filepath)
        if actual_duration is None:
            categories['bad_keywords'].append({
                'path': filepath,
                'label': label,
                'track_id': track_id,
                'reason': 'Cannot read duration'
            })
            continue
        
        filename_lower = filepath.name.lower()
        title_lower = title.lower()
        issues = []
        
        # Check duration constraints
        if actual_duration > 900:  # 15 minutes
            categories['too_long'].append({
                'path': filepath,
                'label': label,
                'track_id': track_id,
                'duration': actual_duration,
                'spotify_duration': spotify_duration_sec,
                'reason': f'{actual_duration/60:.1f} minutes (likely DJ set/podcast)'
            })
            continue
        
        if actual_duration < 30:  # 30 seconds
            categories['too_short'].append({
                'path': filepath,
                'label': label,
                'track_id': track_id,
                'duration': actual_duration,
                'spotify_duration': spotify_duration_sec,
                'reason': f'{actual_duration:.1f} seconds (likely preview/snippet)'
            })
            continue
        
        # Check for bad keywords in filename or title
        found_keywords = []
        for keyword in bad_keywords:
            if keyword in filename_lower or keyword in title_lower:
                # Only flag if the Spotify title doesn't also have this keyword
                if keyword not in title_lower:
                    found_keywords.append(keyword)
        
        if found_keywords:
            categories['bad_keywords'].append({
                'path': filepath,
                'label': label,
                'track_id': track_id,
                'duration': actual_duration,
                'spotify_duration': spotify_duration_sec,
                'keywords': found_keywords,
                'reason': f'Contains: {", ".join(found_keywords)}'
            })
            continue
        
        # Check duration mismatch (warn if >2x or <0.5x Spotify duration)
        if spotify_duration_sec:
            ratio = actual_duration / spotify_duration_sec
            if ratio > 2.5 or ratio < 0.4:
                categories['duration_off'].append({
                    'path': filepath,
                    'label': label,
                    'track_id': track_id,
                    'duration': actual_duration,
                    'spotify_duration': spotify_duration_sec,
                    'ratio': ratio,
                    'reason': f'Duration mismatch: {actual_duration:.0f}s vs Spotify {spotify_duration_sec:.0f}s ({ratio:.1f}x)'
                })
                continue
        
        # Everything looks good
        categories['clean'].append({
            'path': filepath,
            'label': label,
            'track_id': track_id,
            'duration': actual_duration,
            'spotify_duration': spotify_duration_sec,
        })
    
    # Print results
    total_issues = sum(len(v) for k, v in categories.items() if k != 'clean')
    
    if categories['too_long']:
        print(f"\n🚨 TOO LONG ({len(categories['too_long'])} files - likely DJ sets/podcasts):")
        print("-" * 100)
        for item in categories['too_long']:
            print(f"  ❌ {item['label']}")
            spotify_dur = f"{item['spotify_duration']/60:.1f}" if item['spotify_duration'] else "?"
            print(f"     Duration: {item['duration']/60:.1f} min | Spotify: {spotify_dur} min")
            print(f"     File: {item['path']}")
            print()
    
    if categories['too_short']:
        print(f"\n⚠️  TOO SHORT ({len(categories['too_short'])} files - likely previews):")
        print("-" * 100)
        for item in categories['too_short']:
            print(f"  ❌ {item['label']}")
            spotify_dur = f"{item['spotify_duration']:.0f}" if item['spotify_duration'] else "?"
            print(f"     Duration: {item['duration']:.1f}s | Spotify: {spotify_dur}s")
            print(f"     File: {item['path']}")
            print()
    
    if categories['bad_keywords']:
        print(f"\n⚠️  BAD KEYWORDS ({len(categories['bad_keywords'])} files):")
        print("-" * 100)
        for item in categories['bad_keywords']:
            print(f"  ⚠️  {item['label']}")
            if 'keywords' in item:
                print(f"     Keywords: {', '.join(item['keywords'])}")
            spotify_dur = f"{item['spotify_duration']:.0f}s" if item['spotify_duration'] else "?s"
            print(f"     Duration: {item['duration']:.0f}s | Spotify: {spotify_dur}")
            print(f"     File: {item['path']}")
            print()
    
    if categories['duration_off']:
        print(f"\n⚠️  DURATION MISMATCH ({len(categories['duration_off'])} files):")
        print("-" * 100)
        for item in categories['duration_off']:
            print(f"  ⚠️  {item['label']}")
            print(f"     Duration: {item['duration']:.0f}s | Spotify: {item['spotify_duration']:.0f}s | Ratio: {item['ratio']:.1f}x")
            print(f"     File: {item['path']}")
            print()
    
    if categories['missing']:
        print(f"\n❌ MISSING FILES ({len(categories['missing'])} files):")
        print("-" * 100)
        for item in categories['missing']:
            print(f"  ❌ {item['label']}")
            print(f"     File: {item['path']}")
            print()
    
    # Summary
    print("=" * 100)
    print("\nSUMMARY:")
    print(f"  Total files scanned: {len(rows)}")
    print(f"  ✅ Clean files: {len(categories['clean'])}")
    print(f"  ⚠️  Problematic files: {total_issues}")
    if total_issues > 0:
        print(f"     - Too long (>15 min): {len(categories['too_long'])}")
        print(f"     - Too short (<30 sec): {len(categories['too_short'])}")
        print(f"     - Bad keywords: {len(categories['bad_keywords'])}")
        print(f"     - Duration mismatch: {len(categories['duration_off'])}")
        print(f"     - Missing files: {len(categories['missing'])}")
    print("=" * 100)
    
    # Option to show clean files
    if show_all and categories['clean']:
        print(f"\n✅ CLEAN FILES ({len(categories['clean'])} files):")
        print("-" * 100)
        for item in categories['clean']:
            duration_match = ""
            if item['spotify_duration']:
                diff = abs(item['duration'] - item['spotify_duration'])
                duration_match = f" (Δ {diff:.0f}s)"
            print(f"  ✅ {item['label']} - {item['duration']:.0f}s{duration_match}")
    
    # Offer to delete problematic files
    if total_issues > 0:
        print("\nWould you like to:")
        print("  1. Delete all problematic files")
        print("  2. Delete specific categories")
        print("  3. Do nothing (default)")
        
        choice = input("\nEnter choice (1/2/3): ").strip()
        
        if choice == "1":
            confirm = input(f"\n⚠️  Delete {total_issues} files? (yes/no): ").strip().lower()
            if confirm == "yes":
                deleted = 0
                for category in ['too_long', 'too_short', 'bad_keywords', 'duration_off']:
                    for item in categories[category]:
                        try:
                            item['path'].unlink()
                            # Remove from database
                            conn.execute("DELETE FROM files WHERE file_path = ?", (str(item['path']),))
                            deleted += 1
                            print(f"  Deleted: {item['path'].name}")
                        except Exception as e:
                            print(f"  Error deleting {item['path'].name}: {e}")
                conn.commit()
                print(f"\n✅ Deleted {deleted} files")
        
        elif choice == "2":
            print("\nSelect categories to delete:")
            if categories['too_long']:
                print(f"  1. Too long (>15 min): {len(categories['too_long'])} files")
            if categories['too_short']:
                print(f"  2. Too short (<30 sec): {len(categories['too_short'])} files")
            if categories['bad_keywords']:
                print(f"  3. Bad keywords: {len(categories['bad_keywords'])} files")
            if categories['duration_off']:
                print(f"  4. Duration mismatch: {len(categories['duration_off'])} files")
            
            selections = input("\nEnter numbers separated by commas (e.g., 1,3): ").strip()
            
            category_map = {
                '1': 'too_long',
                '2': 'too_short',
                '3': 'bad_keywords',
                '4': 'duration_off'
            }
            
            selected_categories = [category_map[s.strip()] for s in selections.split(',') if s.strip() in category_map]
            
            if selected_categories:
                total_to_delete = sum(len(categories[cat]) for cat in selected_categories)
                confirm = input(f"\n⚠️  Delete {total_to_delete} files? (yes/no): ").strip().lower()
                
                if confirm == "yes":
                    deleted = 0
                    for cat in selected_categories:
                        for item in categories[cat]:
                            try:
                                item['path'].unlink()
                                conn.execute("DELETE FROM files WHERE file_path = ?", (str(item['path']),))
                                deleted += 1
                                print(f"  Deleted: {item['path'].name}")
                            except Exception as e:
                                print(f"  Error deleting {item['path'].name}: {e}")
                    conn.commit()
                    print(f"\n✅ Deleted {deleted} files")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Scan downloaded music for problematic files")
    parser.add_argument("--all", action="store_true", help="Show all files including clean ones")
    parser.add_argument("--dir", type=str, help="Directory to scan (default: from database)")
    
    args = parser.parse_args()
    
    music_dir = Path(args.dir) if args.dir else None
    scan_for_fluff(music_dir, show_all=args.all)
