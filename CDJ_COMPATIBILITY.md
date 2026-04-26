# CDJ Compatibility: Root Cause and Fix Guide

## Summary

Some `.m4a` files imported into Rekordbox can display unrealistic bitrate values (for example `2822 kbps` or `9216 kbps`) and may fail on CDJs with `Unsupported` errors.

These values are usually parser artifacts, not real encoded audio bitrate.

## Observed Symptoms

- Rekordbox bitrate column shows unexpected fixed values like `2822 kbps` or `9216 kbps`.
- Some tracks with these values fail on CDJ, while other tracks in the same library play.
- A failing track may switch from a bogus fixed value to `VBR` after normalization.

## What Is Actually Happening

The problem is not primarily "bitrate quality". The issue is stream/container compatibility.

Common problematic variants found:

- `major_brand=dash` M4A/MP4 flavor.
- Non-AAC stream inside `.m4a` (for example E-AC-3 / `ec-3`).
- Multichannel audio (for example 5.1) in `.m4a`.
- Container/atom layouts that desktop players parse but CDJ firmware is stricter about.

Example parser-artifact math:

- `2822 kbps` ~= `44,100 * 2 * 32` (stereo 32-bit PCM equivalent)
- `9216 kbps` ~= `48,000 * 6 * 32` (6-channel 32-bit PCM equivalent)

This explains why displayed bitrate can be wrong even when the file is not actually encoded at those rates.

## Proven Working Output Shape

Tracks that consistently worked were normalized to:

- Audio codec: `AAC-LC`
- Channels: `2` (stereo)
- Sample rate: `44100` (or `48000` if needed)
- Container brand: `isom`/`M4A`
- Attached artwork stream preserved when available

## In-Place Single-Track Fix (Manual)

Use this when a specific track fails and you want a direct CDJ retest.

```bash
cd /Users/pete/Documents/Music

in='Track Name [TRACKID].m4a'
tmp='Track Name [TRACKID].cdjfix_tmp.m4a'
bak='Track Name [TRACKID].pre_cdjfix_backup.m4a'

# Keep backup once
[ -f "$bak" ] || cp -f "$in" "$bak"

# Rebuild to CDJ-safe profile with artwork preserved when present
/opt/homebrew/bin/ffmpeg -y -v error \
  -i "$in" \
  -map 0:a:0 \
  -map 0:v:0? \
  -c:a aac -b:a 128k -ar 44100 -ac 2 \
  -c:v copy -disposition:v attached_pic \
  -movflags +faststart -map_metadata 0 -brand isom \
  "$tmp"

mv -f "$tmp" "$in"
```

## Bulk Fix Script

A reusable scanner/fixer script exists at:

- `scripts/fix_cdj_incompatible_m4a.py`

It will:

- Recursively scan a music folder for `.m4a` files.
- Detect files that need normalization.
- Create per-file backups (`.pre_cdjfix_backup.m4a`).
- Fix in place.

Run:

```bash
cd /Users/pete/Documents/GitHub/LightSync
./.venv/bin/python scripts/fix_cdj_incompatible_m4a.py --root /Users/pete/Documents/Music --aac-kbps 128
```

## Downloader Hardening (Future Prevention)

`downloader.py` was updated so normalization is triggered for `.m4a` files that are likely to fail on CDJs, including:

- `dash` branded containers
- Non-`aac` audio codec
- Non-`LC` profile
- Channels greater than 2
- Sample rate outside `44100`/`48000`

Normalization output now also:

- Preserves attached artwork when present
- Forces `isom` brand
- Keeps metadata
- Forces stereo AAC-LC output

## Recommended Test Workflow

After fixing a track:

1. Remove old version from Rekordbox device/export if present.
2. Re-import the updated file.
3. Re-analyze the track.
4. Re-export to USB.
5. Test on CDJ.

## Notes

- `VBR` in Rekordbox is not itself an error.
- Bogus fixed bitrate values are a useful warning signal, but not a guaranteed fail predictor by themselves.
- A track can sometimes play even with a weird displayed value depending on firmware/parser tolerance.
