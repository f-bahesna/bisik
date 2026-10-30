#!/bin/bash
# Compile Bisik.app into build/Bisik.app.
# The app is signed with a local self-signed certificate so its identity, and therefore the
# macOS permissions granted to it, stay the same across builds. Falls back to an ad-hoc signature.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD="$DIR/build/Bisik.app"
IDENTITY="Bisik Local Signing"
KEYCHAIN="$HOME/Library/Keychains/bisik-signing.keychain-db"
P12_PASSWORD="bisik"

if ! command -v swiftc >/dev/null 2>&1; then
    echo "swiftc not found. Install the command line tools with: xcode-select --install" >&2
    exit 1
fi

# Creates the dedicated signing keychain and certificate once. It has an empty password,
# so signing never prompts, and it does not touch the login keychain.
ensure_identity() {
    if [[ -f "$KEYCHAIN" ]] && security find-certificate -c "$IDENTITY" "$KEYCHAIN" >/dev/null 2>&1; then
        security unlock-keychain -p "" "$KEYCHAIN"
        trust_identity
        return 0
    fi

    local tmp
    tmp="$(mktemp -d)"
    cat > "$tmp/openssl.cnf" <<EOF
[req]
distinguished_name = dn
x509_extensions = codesign
prompt = no
[dn]
CN = $IDENTITY
[codesign]
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
basicConstraints = critical, CA:false
EOF
    /usr/bin/openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -config "$tmp/openssl.cnf" \
        -keyout "$tmp/key.pem" -out "$tmp/cert.pem" 2>/dev/null
    /usr/bin/openssl pkcs12 -export -inkey "$tmp/key.pem" -in "$tmp/cert.pem" \
        -out "$tmp/identity.p12" -passout "pass:$P12_PASSWORD"

    rm -f "$KEYCHAIN"
    security create-keychain -p "" "$KEYCHAIN"
    security set-keychain-settings "$KEYCHAIN"
    security unlock-keychain -p "" "$KEYCHAIN"
    security import "$tmp/identity.p12" -k "$KEYCHAIN" -P "$P12_PASSWORD" -T /usr/bin/codesign >/dev/null
    security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "" "$KEYCHAIN" >/dev/null
    rm -rf "$tmp"
    trust_identity
}

# codesign only accepts a self-signed certificate that is trusted for code signing.
# This asks for the login password once, and only changes trust for this one certificate.
trust_identity() {
    if security find-identity -v -p codesigning "$KEYCHAIN" | grep -q "$IDENTITY"; then
        return 0
    fi
    local cert
    cert="$(mktemp)"
    security find-certificate -c "$IDENTITY" -p "$KEYCHAIN" > "$cert"
    security add-trusted-cert -r trustRoot -p codeSign -k "$KEYCHAIN" "$cert"
    rm -f "$cert"
}

rm -rf "$BUILD"
mkdir -p "$BUILD/Contents/MacOS" "$BUILD/Contents/Resources"
swiftc -O -swift-version 5 "$DIR/app/Bisik.swift" -o "$BUILD/Contents/MacOS/Bisik"
cp "$DIR/app/Info.plist" "$BUILD/Contents/Info.plist"
cp "$DIR/bisik.py" "$DIR/requirements.txt" "$BUILD/Contents/Resources/"

if ensure_identity && codesign --force --deep --sign "$IDENTITY" --keychain "$KEYCHAIN" "$BUILD"; then
    echo "Signed with \"$IDENTITY\""
else
    echo "Warning: could not sign with a local certificate, using an ad-hoc signature." >&2
    echo "Permissions must be granted again after every build." >&2
    codesign --force --deep --sign - "$BUILD"
fi
echo "Built $BUILD"
