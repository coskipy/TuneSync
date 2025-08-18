# rekordbox_export.py
from __future__ import annotations
from pathlib import Path
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Dict, Optional

from db import get_conn, resolve_path


def _kind_for_ext(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    return {
        "m4a": "M4A File",
        "mp4": "M4A File",
        "aac": "AAC File",
        "mp3": "MP3 File",
        "flac": "FLAC File",
        "wav": "WAVE File",
        "aiff": "AIFF File",
        "aif": "AIFF File",
        "alac": "ALAC File",
    }.get(ext, f"{ext.upper()} File")


def _file_url_localhost(p: Path) -> str:
    # Spec expects file://localhost/... URIs.
    # Encode path segments but keep slashes.
    abs_posix = p.resolve().as_posix()
    encoded = urllib.parse.quote(abs_posix, safe="/")
    return f"file://localhost{encoded}"


def export_rekordbox_xml(
    download_root: Path,
    out_xml: Optional[Path] = None,
    alias_map: Optional[Dict[str, str]] = None,
    product_name: str = "CapSize",
    product_version: str = "0.1",
) -> Path:
    """
    COLLECTION: only tracks referenced by playlists (and present on disk)
    PLAYLISTS: Spotify order (added_at ASC)
    Paths: file://localhost/… ; Kind & Size filled
    Node attributes per Pioneer spec so RB shows playlist contents.
    """
    conn = get_conn()
    out_xml = out_xml or (download_root / "rekordbox.xml")

    # Fetch playlists
    playlists = conn.execute("SELECT id, name FROM playlists ORDER BY name").fetchall()
    playlist_ids = [p["id"] for p in playlists]

    # Build COLLECTION source rows (distinct, only with files)
    if playlist_ids:
        qmarks = ",".join("?" for _ in playlist_ids)
        coll_rows = conn.execute(f"""
            SELECT DISTINCT t.id AS spid,
                            t.name,
                            t.artist,
                            t.album,
                            t.duration_ms,
                            f.file_path
            FROM playlist_tracks pt
            JOIN tracks t ON t.id = pt.track_id
            JOIN files  f ON f.track_id = t.id
            WHERE pt.playlist_id IN ({qmarks})
            ORDER BY t.artist, t.name
        """, tuple(playlist_ids)).fetchall()
    else:
        coll_rows = []

    # Map Spotify track_id -> Rekordbox TrackID
    trackid_map: Dict[str, int] = {}
    collection = ET.Element("COLLECTION", Entries=str(len(coll_rows)))
    next_id = 1

    for r in coll_rows:
        abs_path = resolve_path(download_root, r["file_path"]).resolve()
        if not abs_path.exists():
            continue
        trackid_map[r["spid"]] = next_id
        next_id += 1

        total_sec = int((r["duration_ms"] or 0) / 1000)
        kind = _kind_for_ext(abs_path.suffix)
        size_bytes = str(abs_path.stat().st_size)

        ET.SubElement(collection, "TRACK", {
            "TrackID": str(trackid_map[r["spid"]]),
            "Name": r["name"],
            "Artist": r["artist"],
            "Album": r["album"] or "",
            "Genre": "",
            "Kind": kind,
            "Size": size_bytes,
            "TotalTime": str(total_sec),
            "TrackNumber": "0",
            "Year": "",
            "Location": _file_url_localhost(abs_path),
        })

    # Build PLAYLISTS tree
    playlists_root = ET.Element("PLAYLISTS")

    # IMPORTANT per spec: root folder node must be Name="ROOT", Type="0" and include Count
    root_children = []
    # We'll construct nodes first, then set Count.
    root_node = ET.SubElement(playlists_root, "NODE", {"Name": "ROOT", "Type": "0", "Count": "0"})

    for pl in playlists:
        display_name = (alias_map or {}).get(pl["id"], pl["name"])

        pl_rows = conn.execute("""
            SELECT t.id AS spid
            FROM playlist_tracks pt
            JOIN tracks t ON t.id = pt.track_id
            JOIN files  f ON f.track_id = t.id
            WHERE pt.playlist_id = ?
            ORDER BY COALESCE(pt.added_at, '1970-01-01T00:00:00Z') ASC
        """, (pl["id"],)).fetchall()

        # Filter to keys present in collection
        keys = [trackid_map[r["spid"]] for r in pl_rows if r["spid"] in trackid_map]

        # Per spec: playlist node Type="1" must include Entries and KeyType
        node = ET.SubElement(root_node, "NODE", {
            "Name": display_name,
            "Type": "1",
            "Entries": str(len(keys)),
            "KeyType": "0",  # keys reference TrackID in COLLECTION
        })
        root_children.append(node)

        for k in keys:
            ET.SubElement(node, "TRACK", {"Key": str(k)})

    # Update ROOT Count to actual number of child nodes
    root_node.set("Count", str(len(root_children)))

    # Wrap in DJ_PLAYLISTS
    root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
    ET.SubElement(root, "PRODUCT", {"Name": product_name, "Version": product_version, "Company": "CapSize"})
    root.append(collection)
    root.append(playlists_root)

    # Pretty-print
    def _indent(elem: ET.Element, level: int = 0):
        i = "\n" + level * "  "
        if len(elem):
            if not elem.text or not elem.text.strip():
                elem.text = i + "  "
            for e in elem:
                _indent(e, level + 1)
            if not e.tail or not e.tail.strip():
                elem.tail = i
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = i

    _indent(root)
    ET.ElementTree(root).write(out_xml, encoding="utf-8", xml_declaration=True)
    return out_xml
