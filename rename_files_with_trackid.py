#!/usr/bin/env python3
"""Rename existing files to include track_id suffix"""

import os
from pathlib import Path
from db import get_conn, attach_file

DOWNLOAD_ROOT = Path(os.getenv("DOWNLOAD_ROOT", "/Users/pete/Documents/Music"))

conn = get_conn()

# Get all files from database
files = conn.execute("""
    SELECT f.track_id, f.file_path, t.name, t.artist
    FROM files f
    JOIN tracks t ON f.track_id = t.id
""").fetchall()

print(f"Found {len(files)} files to process\n")

renamed = 0
skipped = 0
errors = 0

for f in files:
    old_path = DOWNLOAD_ROOT / f['file_path']
    
    # Skip if file doesn't exist
    if not old_path.exists():
        skipped += 1
        continue
    
    # Check if filename already has track_id
    track_id_short = f['track_id'][:8]
    if f"[{track_id_short}]" in old_path.name:
        skipped += 1
        continue
    
    # Build new filename with track_id
    # Use old_path.name to get full filename, then split by last dot only
    ext = old_path.suffix
    name_without_ext = old_path.name[:-len(ext)] if ext else old_path.name
    new_name = f"{name_without_ext} [{track_id_short}]{ext}"
    new_path = old_path.parent / new_name
    
    # Skip if target already exists
    if new_path.exists():
        print(f"⚠️  Target exists: {new_name}")
        skipped += 1
        continue
    
    try:
        # Rename file
        old_path.rename(new_path)
        
        # Update database
        attach_file(conn, f['track_id'], new_path, DOWNLOAD_ROOT)
        
        renamed += 1
        if renamed % 100 == 0:
            print(f"  Renamed {renamed}/{len(files)}...")
    except Exception as e:
        print(f"❌ Error renaming {f['file_path']}: {e}")
        errors += 1

conn.commit()

print(f"\n✅ Rename complete!")
print(f"   Renamed: {renamed}")
print(f"   Skipped: {skipped}")
print(f"   Errors: {errors}")
