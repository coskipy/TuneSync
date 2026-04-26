#!/usr/bin/env python3
"""Scan a music folder for CDJ-problematic M4A files and fix them in place."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from downloader import _ffmpeg_transcode_to_m4a, _needs_cdj_m4a_normalization


SKIP_NAME_MARKERS = (
    ".pre_fix_backup.m4a",
    ".pre_fix2_backup.m4a",
    ".pre_cdjfix_backup.m4a",
    ".tmpfix.m4a",
    ".tmpfix2.m4a",
    ".copyfix.m4a",
    ".cdjfix.m4a",
    ".cdjfix_tmp.m4a",
)


def should_skip(path: Path) -> bool:
    name = path.name.lower()
    return any(marker in name for marker in SKIP_NAME_MARKERS)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fix CDJ-incompatible M4A files in place.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("/Users/pete/Documents/Music"),
        help="Root folder to scan recursively (default: /Users/pete/Documents/Music)",
    )
    parser.add_argument(
        "--aac-kbps",
        type=int,
        default=128,
        help="AAC bitrate for normalized outputs (default: 128)",
    )
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    if not root.exists():
        print(f"ERROR: root folder not found: {root}")
        return 2

    files = [p for p in sorted(root.rglob("*.m4a")) if not should_skip(p)]

    print(f"root={root}", flush=True)
    print(f"total_m4a={len(files)}", flush=True)
    print("scanning_for_incompatible_tracks=started", flush=True)

    problematic = []
    for i, path in enumerate(files, start=1):
        if _needs_cdj_m4a_normalization(path):
            problematic.append(path)
        if i % 100 == 0 or i == len(files):
            print(f"scan_progress={i}/{len(files)}", flush=True)

    print(f"problematic_before={len(problematic)}", flush=True)

    fixed = 0
    failed = 0

    for path in problematic:
        tmp = path.with_name(path.stem + ".cdjfix_tmp.m4a")
        backup = path.with_name(path.stem + ".pre_cdjfix_backup.m4a")
        try:
            _ffmpeg_transcode_to_m4a(path, tmp, aac_kbps=args.aac_kbps)
            if not backup.exists():
                shutil.copy2(path, backup)
            tmp.replace(path)
            fixed += 1
            print(f"fixed: {path}", flush=True)
        except Exception as exc:
            failed += 1
            print(f"failed: {path} :: {exc}", flush=True)
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
        if (fixed + failed) % 25 == 0:
            print(f"fix_progress={fixed + failed}/{len(problematic)}", flush=True)

    remaining = [p for p in files if _needs_cdj_m4a_normalization(p)]
    print(f"fixed_count={fixed}", flush=True)
    print(f"failed_count={failed}", flush=True)
    print(f"problematic_after={len(remaining)}", flush=True)
    if remaining:
        print("remaining files:", flush=True)
        for p in remaining[:100]:
            print(f" - {p}", flush=True)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
