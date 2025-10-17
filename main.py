# main.py
# Deps:
#   pip install spotipy python-dotenv yt-dlp mutagen requests
from pathlib import Path
import os
from dotenv import load_dotenv

from db import init_db, get_conn, get_missing_tracks, attach_file
from sync import sync, get_synced_alias_map
from rescan import rescan_existing_files, purge_bad_duration_files
from downloader import download_missing_batch
from tagger import tag_tracks_in_db
from rekordbox_export import export_rekordbox_xml

# Optional: set to True if you still want .m3u8 exports alongside XML
EXPORT_M3U = False
IGNORE_DIRS = {"old"}  # directories to ignore during rescan


def get_download_root() -> Path:
    load_dotenv()
    root = os.getenv("DOWNLOAD_ROOT")
    if not root:
        raise RuntimeError("DOWNLOAD_ROOT is not set in .env")
    return Path(root)


if __name__ == "__main__":
    DOWNLOAD_ROOT = get_download_root()

    # 1) Ensure schema
    init_db()

    # 2) Sync Spotify -> DB (playlists + tracks)
    missing, orphaned = sync()

    # 3) Rescan disk first to attach anything that already exists
    stats = rescan_existing_files(DOWNLOAD_ROOT, ignore_dirs=IGNORE_DIRS)
    print(
        f"🔎 Rescan: scanned {stats.get('scanned',0)}, attached {stats.get('attached',0)}, "
        f"ambiguous {stats.get('ambiguous',0)}, unmatched {stats.get('unmatched',0)}, "
        f"skipped {stats.get('skipped',0)}"
    )

    purge_stats = purge_bad_duration_files(DOWNLOAD_ROOT)
    if purge_stats.get("purged"):
        print(f"♻️  Removed {purge_stats['purged']} mismatched files")

    # 4) Recompute missing after rescan
    conn = get_conn()
    still_missing = get_missing_tracks(conn)
    total = len(still_missing)
    print(f"\nTo download: {total}")
    
    failures = []
    successes = 0
    transcoded = 0

    # 5) Download whatever is still missing (with per-track progress)
    if total:
        results = download_missing_batch(still_missing, DOWNLOAD_ROOT, aac_kbps=192, progress=True)
        for r in results:
            if r.ok:
                rel = r.final_path.relative_to(DOWNLOAD_ROOT)
                attach_file(conn, r.track_id, rel, DOWNLOAD_ROOT)  # store RELATIVE path
                conn.commit()
                successes += 1
                if r.transcoded:
                    transcoded += 1
            else:
                failures.append(r)
    else:
        print("👍 Nothing to download — library is up to date.")

    # 6) Tag all files on disk with Spotify metadata + cover
    tag_stats = tag_tracks_in_db(DOWNLOAD_ROOT)
    print(
        f"\n🖊️  Tagging: {tag_stats['tagged']} tagged, "
        f"{tag_stats['skipped']} skipped, {tag_stats['errors']} errors"
    )

    # 7) Export Rekordbox XML (auto-sync on RB startup)
    xml_custom = os.getenv("REKORDBOX_XML_PATH")
    alias_map = get_synced_alias_map()
    xml_path = export_rekordbox_xml(DOWNLOAD_ROOT, out_xml=Path(xml_custom) if xml_custom else None, alias_map=alias_map)
    print(f"🧭 Rekordbox XML written to: {xml_path.as_posix()}")

    # (Optional) .m3u8 export alongside XML
    if EXPORT_M3U:
        from playlist_export import export_all_m3u  # lazy import to avoid unused dep when off
        n = export_all_m3u(DOWNLOAD_ROOT, alias_map=alias_map, purge_existing=True)  # writes to DOWNLOAD_ROOT/Playlists
        print(f"📄 Also exported {n} M3U playlists.")

    # 8) Final summary
    not_found = [f for f in failures if (f.error or "").lower().startswith("no suitable youtube match")]
    other_errs = [f for f in failures if f not in not_found]

    print("\n================ SUMMARY ================")
    print(f"✅ Downloaded: {successes}  (transcoded: {transcoded})")
    print(f"❌ Failed:     {len(failures)}  |  Not found: {len(not_found)}  Other errors: {len(other_errs)}")

    if not_found:
        print("\nTracks not found:")
        for f in not_found[:15]:
            print(f"  - {f.artist} - {f.title} ({f.track_id})  {f.error}")
        if len(not_found) > 15:
            print(f"  … and {len(not_found)-15} more")

    if other_errs:
        print("\nOther errors:")
        for f in other_errs[:10]:
            print(f"  - {f.artist} - {f.title} ({f.track_id})  {f.error}")
        if len(other_errs) > 10:
            print(f"  … and {len(other_errs)-10} more")

    print("=========================================\n")
    print("✅ Done.")
