#!/usr/bin/env python3
"""Convert all .m4a files in the music folder to MP3 192kbps in place."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from downloader import _ffmpeg_transcode_to_mp3

SKIP_MARKERS = (".pre_cdjfix_backup.m4a", ".pre_fix_backup.m4a", ".tmpfix.m4a", ".cdjfix_tmp.m4a")


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert all .m4a files to MP3 192kbps in place.")
    parser.add_argument("--root", type=Path, default=Path("/Users/pete/Documents/Music"))
    parser.add_argument("--kbps", type=int, default=192)
    parser.add_argument("--dry-run", action="store_true", help="List files without converting")
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    if not root.exists():
        print(f"ERROR: folder not found: {root}")
        return 2

    files = [
        p for p in sorted(root.rglob("*.m4a"))
        if not any(marker in p.name for marker in SKIP_MARKERS)
    ]

    print(f"Found {len(files)} .m4a files under {root}")
    if args.dry_run:
        for f in files:
            print(f"  {f}")
        return 0

    fixed = failed = 0
    for i, path in enumerate(files, 1):
        out = path.with_suffix(".mp3")
        try:
            _ffmpeg_transcode_to_mp3(path, out, kbps=args.kbps)
            path.unlink()
            fixed += 1
            print(f"[{i}/{len(files)}] ✓ {path.name}")
        except Exception as exc:
            failed += 1
            print(f"[{i}/{len(files)}] ✗ {path.name} — {exc}")
            try:
                if out.exists():
                    out.unlink()
            except OSError:
                pass

    print(f"\nDone. Converted: {fixed}  Failed: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
