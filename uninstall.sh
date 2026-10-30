#!/bin/bash
# Stop Bisik and remove its LaunchAgent. The repo and venv are left in place.
set -euo pipefail

LABEL="com.bisik.agent"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
echo "Bisik LaunchAgent removed."
