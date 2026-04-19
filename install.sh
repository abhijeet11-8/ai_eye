#!/bin/bash
# ─────────────────────────────────────────────────────────────────
#  AI Eye — installer for macOS  (v2)
#  • Builds a proper .app so you can add it to Login Items
#  • No terminal needed to launch
# ─────────────────────────────────────────────────────────────────
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/.venv"
APP_NAME="AI Eye"
APP_DIR="$DIR/$APP_NAME.app"

echo ""
echo "╔═══════════════════════════════╗"
echo "║     AI Eye — Installer v2     ║"
echo "╚═══════════════════════════════╝"
echo ""

# 1. Python check
PY=$(which python3 2>/dev/null || echo "")
[ -z "$PY" ] && { echo "❌ Python 3 not found. Install from python.org"; exit 1; }
echo "✅ Python: $("$PY" --version)"

# 2. Virtual environment
echo "📦 Creating virtual environment…"
"$PY" -m venv "$VENV"

# 3. Dependencies
echo "📦 Installing dependencies…"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$DIR/requirements.txt"
echo "✅ Dependencies installed"

# 4. Ollama (optional)
echo ""
read -rp "Install Ollama for local models? [Y/n]: " ANS
if [[ ! "$ANS" =~ ^[Nn]$ ]]; then
    if command -v ollama &>/dev/null; then
        echo "✅ Ollama already installed"
    elif command -v brew &>/dev/null; then
        brew install ollama
    else
        echo "⚠️  Homebrew not found. Download Ollama from https://ollama.com"
    fi
    echo ""
    read -rp "Pull a model? (e.g. gemma3, llama3.2, mistral) or blank to skip: " MODEL
    [ -n "$MODEL" ] && ollama pull "$MODEL" || true
fi

# 5. Build .app bundle ─────────────────────────────────────────────
echo ""
echo "🔨 Building $APP_NAME.app…"

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"

# Info.plist — LSUIElement=true hides the Dock icon; only menu bar appears
cat > "$APP_DIR/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>          <string>AI Eye</string>
  <key>CFBundleDisplayName</key>   <string>AI Eye</string>
  <key>CFBundleIdentifier</key>    <string>com.local.aieye</string>
  <key>CFBundleVersion</key>       <string>2.0</string>
  <key>CFBundlePackageType</key>   <string>APPL</string>
  <key>CFBundleExecutable</key>    <string>launch</string>
  <key>CFBundleIconFile</key>      <string>AppIcon</string>
  <key>LSUIElement</key>           <true/>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSScreenCaptureUsageDescription</key>
  <string>AI Eye needs screen recording to analyze your screen.</string>
</dict>
</plist>
PLIST

# The executable inside the bundle — wraps our python script
# Uses the venv that lives next to the .app (in the project folder)
cat > "$APP_DIR/Contents/MacOS/launch" << 'LAUNCHER'
#!/bin/bash
# Resolve project dir (three levels up from Contents/MacOS/)
PROJ="$(cd "$(dirname "$0")/../../.." && pwd)"

# Start Ollama in background if installed and not running
if command -v ollama &>/dev/null && ! pgrep -x ollama >/dev/null; then
    ollama serve &>/dev/null &
fi

exec "$PROJ/.venv/bin/python" "$PROJ/ai_eye.py"
LAUNCHER
chmod +x "$APP_DIR/Contents/MacOS/launch"

# Simple eye icon (best-effort — sips + iconutil)
python3 - "$APP_DIR/Contents/Resources/AppIcon.icns" << 'PYICON' 2>/dev/null || true
import struct, zlib, os, sys, tempfile, subprocess

def make_png(size=64):
    def chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xffffffff
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', crc)
    cx = cy = size // 2
    rows = []
    for y in range(size):
        row = b'\x00'
        for x in range(size):
            dx, dy = x - cx, y - cy
            d = (dx*dx + dy*dy) ** 0.5
            r = size // 2
            if d < r - 1:
                if d < r * 0.22:   row += b'\x10\x10\x18\xff'
                elif d < r * 0.46: row += b'\x3b\x82\xf6\xff'
                else:              row += b'\x1e\x1e\x2a\xff'
            else:
                row += b'\x00\x00\x00\x00'
        rows.append(row)
    raw = b''.join(rows)
    filt = zlib.compress(raw, 9)
    png  = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0))
    png += chunk(b'IDAT', filt)
    png += chunk(b'IEND', b'')
    return png

out_icns = sys.argv[1]
iconset  = tempfile.mkdtemp(suffix='.iconset')
for sz in (16, 32, 64, 128, 256, 512):
    p = os.path.join(iconset, f'icon_{sz}x{sz}.png')
    with open(p, 'wb') as f:
        f.write(make_png(sz))
subprocess.run(['iconutil', '-c', 'icns', iconset, '-o', out_icns],
               check=True, capture_output=True)
import shutil; shutil.rmtree(iconset, ignore_errors=True)
PYICON

echo "✅ $APP_NAME.app built"

# 6. Copy to /Applications ─────────────────────────────────────────
echo ""
read -rp "Copy to /Applications? [Y/n]: " COPY_ANS
if [[ ! "$COPY_ANS" =~ ^[Nn]$ ]]; then
    cp -r "$APP_DIR" "/Applications/$APP_NAME.app"
    echo "✅ Copied to /Applications"
    FINAL_APP="/Applications/$APP_NAME.app"
else
    FINAL_APP="$APP_DIR"
fi

# 7. Login Item (auto-start) ────────────────────────────────────────
echo ""
read -rp "Add to Login Items so AI Eye starts automatically? [Y/n]: " LOGIN_ANS
if [[ ! "$LOGIN_ANS" =~ ^[Nn]$ ]]; then
    osascript << APPLE
tell application "System Events"
    make new login item at end of login items with properties ¬
        {path:"$FINAL_APP", hidden:true, name:"AI Eye"}
end tell
APPLE
    echo "✅ Added to Login Items"
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ✅  Done!                                                   ║"
echo "║                                                              ║"
echo "║  Launch:  open '$FINAL_APP'                                  ║"
echo "║           — double-click AI Eye.app in Finder                ║"
echo "║           — or it auto-starts at login (if you chose yes)    ║"
echo "║                                                              ║"
echo "║  ⚠️  Grant once in System Settings → Privacy & Security:    ║"
echo "║     Screen Recording  →  enable AI Eye                       ║"
echo "║                                                              ║"
echo "║  Minimize: click  −  in the panel header → floating 👁 bubble║"
echo "║  Restore:  click the bubble  OR  the 👁 menu bar icon        ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
