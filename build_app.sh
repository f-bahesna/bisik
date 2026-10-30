#!/bin/bash
# Compile Bisik.app into build/Bisik.app (ad-hoc signed).
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD="$DIR/build/Bisik.app"

if ! command -v swiftc >/dev/null 2>&1; then
    echo "swiftc not found. Install the command line tools with: xcode-select --install" >&2
    exit 1
fi

rm -rf "$BUILD"
mkdir -p "$BUILD/Contents/MacOS" "$BUILD/Contents/Resources"
swiftc -O -swift-version 5 "$DIR/app/Bisik.swift" -o "$BUILD/Contents/MacOS/Bisik"
cp "$DIR/app/Info.plist" "$BUILD/Contents/Info.plist"
cp "$DIR/bisik.py" "$DIR/requirements.txt" "$BUILD/Contents/Resources/"
codesign --force --deep --sign - "$BUILD"
echo "Built $BUILD"
