#!/usr/bin/env python3
"""Retag all files and regenerate Rekordbox XML"""

import os
from pathlib import Path
from tagger import tag_tracks_in_db
from rekordbox_export import export_rekordbox_xml

DOWNLOAD_ROOT = Path(os.getenv("DOWNLOAD_ROOT", "/Users/pete/Documents/Music"))

print("=" * 80)
print("RETAGGING ALL FILES")
print("=" * 80)

# Retag all files in the database
result = tag_tracks_in_db(DOWNLOAD_ROOT, track_ids=None, progress=True)

print(f"\n✅ Tagging complete!")
print(f"   Tagged: {result['tagged']}")
print(f"   Skipped: {result['skipped']}")
print(f"   Errors: {result['errors']}")

print("\n" + "=" * 80)
print("REGENERATING REKORDBOX XML")
print("=" * 80)

# Export Rekordbox XML
xml_custom = os.getenv("REKORDBOX_XML_PATH")
xml_path = export_rekordbox_xml(
    DOWNLOAD_ROOT, 
    out_xml=Path(xml_custom) if xml_custom else None
)

print(f"\n✅ Exported Rekordbox library → {xml_path}")
print("\n🎉 All done! Your library is now up to date.")
