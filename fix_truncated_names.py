#!/usr/bin/env python3
"""Fix truncated filenames by rebuilding from track metadata"""

import os
from pathlib import Path
from db import get_conn, attach_file
from downloader import _sanitize

DOWNLOAD_ROOT = Path(os.getenv("DOWNLOAD_ROOT", "/Users/pete/Documents/Music"))

conn = get_conn()

# Get all files with track info
files = conn.execute("""
    SELECT f.track_id, f.file_path, t.name, t.artist
    FROM files f
    JOIN tracks t ON f.track_id = t.id
""").fetchall()

print(f"Checking {len(files)} files for truncation issues...\n")

fixed = 0
skipped = 0
errors = 0

for f in files:
    old_path = DOWNLOAD_ROOT / f['file_path']
    
    # Skip if file doesn't exist
    if not old_path.exists():
        skipped += 1
        continue
    
    # Build what the filename SHOULD be
    track_id_short = f['track_id'][:8]
    expected_label = f"{f['artist']} - {f['name']}"
    expected_base = _sanitize(expected_label)
    expected_name = f"{expected_base} [{track_id_short}]{old_path.suffix}"
    
    # If current filename matches expected, skip
    if old_path.name == expected_name:
        skipped += 1
        continue
    
    new_path = old_path.parent / expected_name
    
    # Skip if target already exists
    if new_path.exists() and new_path != old_path:
        print(f"⚠️  Target exists: {expected_name}")
        skipped += 1
        continue
    
    try:
        # Rename file
        print(f"Fixing: {old_path.name}")
        print(f"     → {expected_name}")
        old_path.rename(new_path)
        
        # Update database
        attach_file(conn, f['track_id'], new_path, DOWNLOAD_ROOT)
        
        fixed += 1
        if fixed % 50 == 0:
            conn.commit()
            print(f"  Fixed {fixed} files...")
    except Exception as e:
        print(f"❌ Error renaming {f['file_path']}: {e}")
        errors += 1

conn.commit()

print(f"\n✅ Fix complete!")
print(f"   Fixed: {fixed}")
print(f"   Skipped: {skipped}")
print(f"   Errors: {errors}")
