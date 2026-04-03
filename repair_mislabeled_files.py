#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from db import get_conn, resolve_path
from tagger import tag_tracks_in_db


MARKER_RE = re.compile(r"\[([A-Za-z0-9]{8})\]")


@dataclass
class RemapCandidate:
    current_track_id: str
    target_track_id: str
    file_path: str


def _track_short(track_id: str) -> str:
    return (track_id or "")[:8]


def _marker_short_from_path(stored_path: str) -> Optional[str]:
    name = Path(stored_path).name
    m = MARKER_RE.search(name)
    if not m:
        return None
    return m.group(1)


def _safe_duration_seconds(path: Path) -> Optional[float]:
    try:
        from mutagen import File as MutagenFile

        m = MutagenFile(path)
        if m is not None and getattr(m, "info", None) and getattr(m.info, "length", None):
            return float(m.info.length)
    except Exception:
        pass
    return None


def _build_short_id_map(conn) -> dict[str, str]:
    rows = conn.execute("SELECT id FROM tracks").fetchall()
    buckets: dict[str, list[str]] = {}
    for r in rows:
        tid = r["id"]
        buckets.setdefault(_track_short(tid), []).append(tid)

    out: dict[str, str] = {}
    for short_id, ids in buckets.items():
        if len(ids) == 1:
            out[short_id] = ids[0]
    return out


def _find_remap_candidates(conn) -> list[RemapCandidate]:
    short_to_track = _build_short_id_map(conn)
    rows = conn.execute("SELECT track_id, file_path FROM files").fetchall()

    cands: list[RemapCandidate] = []
    for r in rows:
        current = r["track_id"]
        stored = r["file_path"]
        marker = _marker_short_from_path(stored)
        if not marker:
            continue
        target = short_to_track.get(marker)
        if not target:
            continue
        if target != current:
            cands.append(
                RemapCandidate(
                    current_track_id=current,
                    target_track_id=target,
                    file_path=stored,
                )
            )
    return cands


def _target_current_if_any(conn, target_track_id: str) -> Optional[str]:
    row = conn.execute("SELECT file_path FROM files WHERE track_id = ?", (target_track_id,)).fetchone()
    return row["file_path"] if row else None


def _apply_remaps(conn, cands: list[RemapCandidate]) -> tuple[list[str], list[RemapCandidate]]:
    fixed_track_ids: set[str] = set()
    skipped: list[RemapCandidate] = []

    # Work one-by-one in current DB state.
    for c in cands:
        current_path_row = conn.execute(
            "SELECT file_path FROM files WHERE track_id = ?",
            (c.current_track_id,),
        ).fetchone()
        if not current_path_row:
            continue

        current_path = current_path_row["file_path"]
        target_path = _target_current_if_any(conn, c.target_track_id)

        if target_path is None:
            # Safe simple remap: move file binding from current -> target.
            conn.execute("DELETE FROM files WHERE track_id = ?", (c.current_track_id,))
            conn.execute(
                "INSERT INTO files (track_id, file_path) VALUES (?, ?)",
                (c.target_track_id, current_path),
            )
            fixed_track_ids.add(c.target_track_id)
            continue

        if target_path == current_path:
            # Both track IDs currently point at the same file path. Keep the marker-backed
            # target mapping and drop the incorrect duplicate binding.
            conn.execute("DELETE FROM files WHERE track_id = ?", (c.current_track_id,))
            fixed_track_ids.add(c.target_track_id)
            continue

        # Conservative swap only if markers indicate a likely crossed pair.
        target_marker = _marker_short_from_path(target_path)
        if target_marker == _track_short(c.current_track_id):
            conn.execute(
                "UPDATE files SET file_path = ? WHERE track_id = ?",
                (target_path, c.current_track_id),
            )
            conn.execute(
                "UPDATE files SET file_path = ? WHERE track_id = ?",
                (current_path, c.target_track_id),
            )
            fixed_track_ids.add(c.current_track_id)
            fixed_track_ids.add(c.target_track_id)
        else:
            skipped.append(c)

    return sorted(fixed_track_ids), skipped


def _find_duration_outliers(conn, download_root: Path, threshold_sec: int = 15) -> list[tuple[str, str, float, float]]:
    rows = conn.execute(
        """
        SELECT f.track_id, f.file_path, t.duration_ms
        FROM files f
        JOIN tracks t ON t.id = f.track_id
        """
    ).fetchall()

    out: list[tuple[str, str, float, float]] = []
    for r in rows:
        expected_ms = r["duration_ms"]
        if not expected_ms:
            continue
        abs_path = resolve_path(download_root, r["file_path"])
        if not abs_path.exists():
            continue
        actual_sec = _safe_duration_seconds(abs_path)
        if actual_sec is None:
            continue
        expected_sec = float(expected_ms) / 1000.0
        delta = abs(actual_sec - expected_sec)
        if delta >= float(threshold_sec):
            out.append((r["track_id"], r["file_path"], actual_sec, expected_sec))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repair mismatched file<->track links using filename track-id markers."
    )
    parser.add_argument(
        "--download-root",
        default=(os.getenv("DOWNLOAD_ROOT") or "").strip() or str(Path.home() / "Music"),
        help="Download root used for relative file paths.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply DB remaps. Without this, runs as dry-run.",
    )
    parser.add_argument(
        "--retag",
        action="store_true",
        help="Retag repaired tracks after apply.",
    )
    parser.add_argument(
        "--duration-threshold-sec",
        type=int,
        default=15,
        help="Report files whose actual duration differs from DB by this many seconds.",
    )
    args = parser.parse_args()

    download_root = Path(args.download_root).expanduser()
    conn = get_conn()

    try:
        candidates = _find_remap_candidates(conn)
        print(f"Marker-based remap candidates: {len(candidates)}")

        if not args.apply:
            for c in candidates[:40]:
                print(
                    f"  DRY-RUN: {c.current_track_id} -> {c.target_track_id}"
                    f"  ({c.file_path})"
                )
            if len(candidates) > 40:
                print(f"  ... and {len(candidates) - 40} more")
        else:
            fixed_track_ids, skipped = _apply_remaps(conn, candidates)
            conn.commit()
            print(f"Applied remaps/reassignments for track IDs: {len(fixed_track_ids)}")
            if skipped:
                print(f"Skipped ambiguous/conflicting candidates: {len(skipped)}")
                for c in skipped[:20]:
                    print(
                        f"  SKIP: {c.current_track_id} -> {c.target_track_id}"
                        f"  ({c.file_path})"
                    )
                if len(skipped) > 20:
                    print(f"  ... and {len(skipped) - 20} more")

            if args.retag and fixed_track_ids:
                print("Retagging repaired tracks...")
                stats = tag_tracks_in_db(download_root, track_ids=fixed_track_ids, progress=True)
                print(
                    "Retag stats: "
                    f"tagged={stats['tagged']} skipped={stats['skipped']} errors={stats['errors']}"
                )

        outliers = _find_duration_outliers(
            conn,
            download_root,
            threshold_sec=max(1, int(args.duration_threshold_sec)),
        )
        print(f"Duration outliers (>= {args.duration_threshold_sec}s): {len(outliers)}")
        for tid, path, actual, expected in outliers[:30]:
            print(
                f"  OUTLIER: {tid} | {path} | actual={actual:.1f}s expected={expected:.1f}s"
            )
        if len(outliers) > 30:
            print(f"  ... and {len(outliers) - 30} more")

    finally:
        try:
            conn.close()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
