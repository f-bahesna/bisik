#!/bin/bash
# Setup venv, install dependencies, and register Bisik as a LaunchAgent that starts at login.
set -euo pipefail

LABEL="com.bisik.agent"
DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/bisik.log"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    echo "Bisik requires macOS on Apple Silicon." >&2
    exit 1
fi

PYTHON=""
for candidate in python3.12 python3.13 python3.11 python3.10; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON="$(command -v "$candidate")"
        break
    fi
done
if [[ -z "$PYTHON" ]]; then
    echo "Python 3.10-3.13 not found. Install one with: brew install python@3.12" >&2
    exit 1
fi

echo "Creating virtual environment with $PYTHON ..."
"$PYTHON" -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q --upgrade pip
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$DIR/.venv/bin/python</string>
        <string>$DIR/bisik.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG</string>
    <key>StandardErrorPath</key>
    <string>$LOG</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

REAL_PYTHON="$("$DIR/.venv/bin/python" -c 'import os, sys; print(os.path.realpath(sys.executable))')"
echo "Bisik installed and running. Log: $LOG"
echo
echo "Grant Microphone, Accessibility and Input Monitoring to this binary in"
echo "System Settings > Privacy & Security (use '+' and press Cmd+Shift+G to paste the path):"
echo "  $REAL_PYTHON"
