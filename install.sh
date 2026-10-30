#!/bin/bash
# Build Bisik.app, install it to ~/Applications, and start it at every login.
set -euo pipefail

LABEL="com.bisik.agent"
DIR="$(cd "$(dirname "$0")" && pwd)"
SUPPORT="$HOME/Library/Application Support/Bisik"
APP="$HOME/Applications/Bisik.app"
BUILD="$DIR/build/Bisik.app"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    echo "Bisik requires macOS on Apple Silicon." >&2
    exit 1
fi
if ! command -v swiftc >/dev/null 2>&1; then
    echo "swiftc not found. Install the command line tools with: xcode-select --install" >&2
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
mkdir -p "$SUPPORT"
"$PYTHON" -m venv "$SUPPORT/venv"
"$SUPPORT/venv/bin/pip" install -q --upgrade pip
"$SUPPORT/venv/bin/pip" install -q -r "$DIR/requirements.txt"

echo "Building Bisik.app ..."
"$DIR/build_app.sh"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
pkill -x Bisik 2>/dev/null || true
pkill -f "Bisik.app/Contents/Resources/bisik.py" 2>/dev/null || true
mkdir -p "$HOME/Applications" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
rm -rf "$APP"
cp -R "$BUILD" "$APP"

# The ad-hoc signature changes with every build, which invalidates earlier permission grants.
for service in Accessibility ListenEvent Microphone; do
    tccutil reset "$service" com.bisik.app >/dev/null 2>&1 || true
done

# Make fn a plain key so a double tap does not open the emoji picker or Dictation.
defaults write com.apple.HIToolbox AppleFnUsageType -int 0

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/open</string>
        <string>-a</string>
        <string>$APP</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
EOF
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo
echo "Bisik installed at $APP and started (look for the mic icon in the menu bar)."
echo "Allow the Microphone, Accessibility and Input Monitoring prompts that appear."
echo "Log: $HOME/Library/Logs/bisik.log"
