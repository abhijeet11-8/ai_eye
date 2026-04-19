#!/bin/bash
cd "$(dirname "$0")"

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
