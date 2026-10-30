#!/bin/bash
# Stop Bisik and remove the app, its LaunchAgent and its Python environment.
# Downloaded Whisper models in ~/.cache/huggingface are left in place.
set -euo pipefail

LABEL="com.bisik.agent"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
pkill -x Bisik 2>/dev/null || true
pkill -f "Bisik.app/Contents/Resources/bisik.py" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
rm -rf "$HOME/Applications/Bisik.app" "$HOME/Library/Application Support/Bisik"
echo "Bisik removed."
