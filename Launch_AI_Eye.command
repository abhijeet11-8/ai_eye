#!/bin/bash
cd "$(dirname "$0")"

# ── Check that the virtual environment exists ─────────────────────
if [ ! -f ".venv/bin/python" ]; then
    osascript -e 'display alert "AI Eye — Setup required" message "Virtual environment not found.\n\nPlease run the installer first:\n\n  cd ~/Desktop/ai_eye\n  ./install.sh" as critical'
    exit 1
fi

# Kill any existing AI Eye instance first (clean restart)
pkill -f "python.*ai_eye.py" 2>/dev/null || true
sleep 0.3

# Start Ollama in background if not running
command -v ollama &>/dev/null && ! pgrep -x ollama >/dev/null && ollama serve &>/dev/null &

# Launch AI Eye fully detached from this terminal.
# nohup + disown means the process owns itself — closing Terminal won't kill it.
nohup .venv/bin/python ai_eye.py > /tmp/ai_eye.log 2>&1 &
disown $!

# Close this terminal window after AI Eye starts
sleep 1.5
osascript -e 'tell application "Terminal" to close (every window)' 2>/dev/null || true
osascript -e 'tell application "iTerm2" to close (current window)' 2>/dev/null || true
