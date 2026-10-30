#!/bin/bash
# Build build/Bisik.dmg containing Bisik.app and a shortcut to /Applications.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
STAGE="$DIR/build/dmg"
DMG="$DIR/build/Bisik.dmg"

"$DIR/build_app.sh"

rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
cp -R "$DIR/build/Bisik.app" "$STAGE/Bisik.app"
ln -s /Applications "$STAGE/Applications"

hdiutil create -volname "Bisik" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"
echo "Built $DMG ($(du -h "$DMG" | cut -f1))"
