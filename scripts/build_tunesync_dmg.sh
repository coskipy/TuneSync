#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PY="$REPO_ROOT/.venv/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "ERROR: venv python not found at $PY" >&2
  echo "Run setup first to create .venv." >&2
  exit 1
fi

# Build dependencies (only needed on the machine building the DMG)
"$PY" -m pip install -U pip >/dev/null
"$PY" -m pip install -U pyinstaller >/dev/null

# App/runtime dependencies (ensures imageio-ffmpeg ffmpeg binary is available to bundle)
"$PY" -m pip install -U -r requirements.txt >/dev/null

# Clean prior builds
rm -rf build || true
rm -rf dist || true

# Regenerate .icns from the source logo (so updates to the PNG are reflected).
SRC_PNG="$REPO_ROOT/tunesync_app/ui/images/tunesync_logo.png"
OUT_ICNS="$REPO_ROOT/assets/TuneSync.icns"
ICONSET_DIR="$REPO_ROOT/build/icon.iconset"

if [[ -f "$SRC_PNG" ]] && command -v iconutil >/dev/null 2>&1 && command -v sips >/dev/null 2>&1; then
  # Rebuild if missing or older than the source PNG.
  if [[ ! -f "$OUT_ICNS" ]] || [[ "$SRC_PNG" -nt "$OUT_ICNS" ]]; then
    mkdir -p "$REPO_ROOT/assets"
    rm -rf "$ICONSET_DIR" || true
    mkdir -p "$ICONSET_DIR"

    sips -z 16 16     "$SRC_PNG" --out "$ICONSET_DIR/icon_16x16.png" >/dev/null
    sips -z 32 32     "$SRC_PNG" --out "$ICONSET_DIR/icon_16x16@2x.png" >/dev/null
    sips -z 32 32     "$SRC_PNG" --out "$ICONSET_DIR/icon_32x32.png" >/dev/null
    sips -z 64 64     "$SRC_PNG" --out "$ICONSET_DIR/icon_32x32@2x.png" >/dev/null
    sips -z 128 128   "$SRC_PNG" --out "$ICONSET_DIR/icon_128x128.png" >/dev/null
    sips -z 256 256   "$SRC_PNG" --out "$ICONSET_DIR/icon_128x128@2x.png" >/dev/null
    sips -z 256 256   "$SRC_PNG" --out "$ICONSET_DIR/icon_256x256.png" >/dev/null
    sips -z 512 512   "$SRC_PNG" --out "$ICONSET_DIR/icon_256x256@2x.png" >/dev/null
    sips -z 512 512   "$SRC_PNG" --out "$ICONSET_DIR/icon_512x512.png" >/dev/null
    sips -z 1024 1024 "$SRC_PNG" --out "$ICONSET_DIR/icon_512x512@2x.png" >/dev/null

    iconutil -c icns "$ICONSET_DIR" -o "$OUT_ICNS"
  fi
fi

# Build .app
"$PY" -m PyInstaller TuneSync.spec

APP="dist/TuneSync.app"
if [[ ! -d "$APP" ]]; then
  echo "ERROR: Expected $APP to be created" >&2
  exit 2
fi

# Ad-hoc sign so macOS is happier for testers (still not notarized)
if command -v codesign >/dev/null 2>&1; then
  /usr/bin/codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
fi

# Create DMG
DMG_OUT="dist/TuneSync.dmg"
STAGE="build/dmg_stage"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -sf /Applications "$STAGE/Applications"

/usr/bin/hdiutil create \
  -volname "TuneSync" \
  -srcfolder "$STAGE" \
  -ov -format UDZO \
  "$DMG_OUT" >/dev/null

echo "✅ Built: $DMG_OUT"