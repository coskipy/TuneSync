#!/usr/bin/env python3
from yt_dlp import YoutubeDL
import re

def search_youtube(query: str, limit: int = 5):
    """Search YouTube and return top results."""
    opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": True,
        "default_search": "ytsearch",
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        return info.get("entries", [])

def list_formats(url: str):
    """List all formats for a given YouTube URL without attempting downloads."""
    opts = {
        "quiet": True,
        "simulate": True,
        "noplaylist": True,
        "forcejson": True,
        "listformats": True,   # tell yt-dlp to *only* dump formats
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        formats = info.get("formats", [])
        print(f"\nAvailable formats for: {info.get('title')} [{info.get('id')}]")
        print("-" * 80)

        for f in formats:
            fid = f.get("format_id") or "?"
            ext = f.get("ext") or "?"
            acodec = f.get("acodec") or "-"
            vcodec = f.get("vcodec") or "-"
            abr = f.get("abr")
            filesize = f.get("filesize") or f.get("filesize_approx")

            if vcodec == "none" and acodec in ("mp4a.40.2", "opus"):
                health = "✅ healthy"
            elif vcodec == "none":
                health = "⚠️ audio (unknown codec)"
            else:
                health = "❌ video"

            size_str = f"{filesize/1024/1024:.1f} MB" if filesize else "?"
            abr_str = f"{abr} kbps" if abr else "?"

            print(f"{fid:>4} | {ext:<4} | {acodec:<10} | {abr_str:<8} | {size_str:<8} | {health}")

        audios = [f for f in formats if f.get("vcodec") == "none"]
        if not audios:
            print("\n⚠️  No audio-only formats found! yt-dlp will fallback to video muxes.")


def main():
    print("🎵 YouTube Format Debugger")
    query = input("Enter song title or YouTube URL: ").strip()

    if re.match(r"^https?://", query):
        # Direct URL
        list_formats(query)
    else:
        # Treat as search
        print(f"\n🔎 Searching for: {query}")
        results = search_youtube(query)
        for i, r in enumerate(results, 1):
            title = r.get("title")
            dur = r.get("duration") or "?"
            url = r.get("url")
            print(f"[{i}] {title}  ({dur}s)")
            print(f"     {url}")

        url = input("\nPaste chosen video URL: ").strip()
        if url:
            list_formats(url)

if __name__ == "__main__":
    main()
