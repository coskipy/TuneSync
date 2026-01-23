# main.py
# Deps:
#   pip install spotipy python-dotenv yt-dlp mutagen requests
from pathlib import Path
import os
import sys
import traceback
from dotenv import load_dotenv

from db import init_db, get_conn, get_missing_tracks, attach_file, mark_track_unavailable
from sync import sync, get_synced_alias_map
from rescan import rescan_existing_files, purge_bad_duration_files
from downloader import download_missing_batch
from tagger import tag_tracks_in_db
from rekordbox_export import export_rekordbox_xml

# Optional: set to True if you still want .m3u8 exports alongside XML
EXPORT_M3U = False
IGNORE_DIRS = {"old"}  # directories to ignore during rescan

MAX_WORKERS = 8 # Number of parallel download workers
DEBUG_SEARCH = False  # Set to True to see detailed search results and scoring

def get_download_root() -> Path:
    load_dotenv()
    root = os.getenv("DOWNLOAD_ROOT")
    if not root:
        raise RuntimeError("DOWNLOAD_ROOT is not set in .env")
    return Path(root)


if __name__ == "__main__":
    try:
        DOWNLOAD_ROOT = get_download_root()

        # 1) Ensure schema
        try:
            init_db()
        except Exception as e:
            print(f"❌ Database initialization failed: {e}")
            traceback.print_exc()
            sys.exit(1)

        # 2) Sync Spotify -> DB (playlists + tracks)
        playlists_changed = False
        try:
            missing, orphaned, playlists_changed = sync()
        except Exception as e:
            print(f"❌ Spotify sync failed: {e}")
            traceback.print_exc()
            print("⚠️  Continuing with existing database...")
            conn = get_conn()
            missing = get_missing_tracks(conn)
            orphaned = []
            playlists_changed = True  # Assume changes if sync failed, to be safe

        # 3) Rescan disk only if needed
        needs_download = len(missing)
        needs_cleanup = 0
        needs_attachment = 0
        
        try:
            if playlists_changed or missing:
                # Full rescan - walk entire directory
                stats = rescan_existing_files(DOWNLOAD_ROOT, ignore_dirs=IGNORE_DIRS)
                needs_attachment = stats.get('attached', 0)
                needs_cleanup = stats.get('removed', 0) + stats.get('fragments_deleted', 0)
                print(
                    f"� Scanned {stats.get('scanned',0)} files: "
                    f"attached {stats.get('attached',0)}, skipped {stats.get('skipped',0)}, "
                    f"removed {stats.get('removed',0)} missing, cleaned {stats.get('fragments_deleted',0)} fragments"
                )
            else:
                # Quick cleanup only
                from rescan import _cleanup_missing_files, _cleanup_fragment_files
                conn = get_conn()
                removed = _cleanup_missing_files(conn, DOWNLOAD_ROOT)
                fragments = _cleanup_fragment_files(DOWNLOAD_ROOT)
                needs_cleanup = removed + fragments
                if removed or fragments:
                    print(f"🧹 Cleaned up: {removed} missing files, {fragments} fragments")
        except Exception as e:
            print(f"❌ Rescan failed: {e}")
            traceback.print_exc()

        # 4) Recompute missing after rescan
        try:
            conn = get_conn()
            still_missing = get_missing_tracks(conn)
            total = len(still_missing)
            
            # Print status summary
            status_parts = []
            if total > 0:
                status_parts.append(f"{total} to download")
            if needs_attachment > 0:
                status_parts.append(f"{needs_attachment} attached")
            if needs_cleanup > 0:
                status_parts.append(f"{needs_cleanup} cleaned")
            
            if status_parts:
                print(f"📊 Status: {', '.join(status_parts)}")
            else:
                print(f"✨ Library is up to date")
                
        except Exception as e:
            print(f"❌ Failed to get missing tracks: {e}")
            traceback.print_exc()
            sys.exit(1)
        
        failures = []
        successes = 0
        transcoded = 0
        newly_downloaded_track_ids = []  # Track which files were just downloaded

        # 5) Download whatever is still missing
        if total:
            print(f"\n⬇️  Downloading {total} tracks...")
            try:
                results = download_missing_batch(still_missing, DOWNLOAD_ROOT, aac_kbps=192, progress=True, max_workers=MAX_WORKERS, debug=DEBUG_SEARCH)
                batch_size = 25  # Commit every 25 files for better performance
                for idx, r in enumerate(results):
                    if r and r.ok:
                        try:
                            rel = r.final_path.relative_to(DOWNLOAD_ROOT)
                            attach_file(conn, r.track_id, rel, DOWNLOAD_ROOT)
                            newly_downloaded_track_ids.append(r.track_id)  # Track new downloads
                            successes += 1
                            if r.transcoded:
                                transcoded += 1
                        except Exception as e:
                            print(f"⚠️  Failed to record {r.title}: {e}")
                            failures.append(r)
                    elif r:
                        # If a track is consistently not found, mark it as unavailable so
                        # it no longer blocks playlists as "missing".
                        try:
                            err_l = (r.error or "").strip().lower()
                            if "no suitable youtube match" in err_l:
                                mark_track_unavailable(conn, r.track_id, reason=r.error)
                        except Exception:
                            pass
                        failures.append(r)
                    
                    # Batch commit every N files
                    if (idx + 1) % batch_size == 0 or idx == len(results) - 1:
                        conn.commit()
                
                print(f"✅ Downloaded {successes}/{total} tracks" + (f" (transcoded: {transcoded})" if transcoded else ""))
            except Exception as e:
                print(f"❌ Download batch failed: {e}")
                traceback.print_exc()
                print("⚠️  Continuing to tagging phase...")

        # 6) Tag only newly downloaded files with Spotify metadata + cover
        try:
            if newly_downloaded_track_ids:
                print(f"\n🏷️  Tagging {len(newly_downloaded_track_ids)} files...")
                tag_stats = tag_tracks_in_db(DOWNLOAD_ROOT, track_ids=newly_downloaded_track_ids)
                print(f"✅ Tagged {tag_stats['tagged']} files" + (f" ({tag_stats['errors']} errors)" if tag_stats['errors'] else ""))
            else:
                tag_stats = {"tagged": 0, "skipped": 0, "errors": 0}
        except Exception as e:
            print(f"❌ Tagging failed: {e}")
            traceback.print_exc()

        # 7) Export Rekordbox XML
        try:
            xml_custom = os.getenv("REKORDBOX_XML_PATH")
            alias_map = get_synced_alias_map()
            xml_path = export_rekordbox_xml(DOWNLOAD_ROOT, out_xml=Path(xml_custom) if xml_custom else None, alias_map=alias_map)
            print(f"\n📀 Exported Rekordbox library → {xml_path.name}")
        except Exception as e:
            print(f"❌ Rekordbox export failed: {e}")
            traceback.print_exc()

        # 8) Final summary (only if there were downloads or failures)
        if total > 0 or failures:
            not_found = [f for f in failures if (f.error or "").lower().startswith("no suitable youtube match")]
            other_errs = [f for f in failures if f not in not_found]

            print("\n" + "="*50)
            print("📊 SUMMARY")
            print("="*50)
            if successes > 0:
                print(f"  ✅ Downloaded: {successes}" + (f" (transcoded: {transcoded})" if transcoded else ""))
            if failures:
                print(f"  ❌ Failed: {len(failures)}" + (f" (not found: {len(not_found)}, errors: {len(other_errs)})" if not_found or other_errs else ""))

            if not_found:
                print(f"\n  Tracks not found on YouTube:")
                for f in not_found[:5]:
                    print(f"    • {f.artist} - {f.title}")
                if len(not_found) > 5:
                    print(f"    ... and {len(not_found)-5} more")

            if other_errs:
                print(f"\n  Download errors:")
                for f in other_errs[:5]:
                    print(f"    • {f.artist} - {f.title}: {f.error}")
                if len(other_errs) > 5:
                    print(f"    ... and {len(other_errs)-5} more")

            print("="*50)

        print("\n✅ Done!")

    except KeyboardInterrupt:
        print("\n\n⏸️  Interrupted by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        traceback.print_exc()
        sys.exit(1)
