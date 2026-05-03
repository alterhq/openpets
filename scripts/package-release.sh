#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${OPENPETS_VERSION:-0.1.0}"
BUILD_NUMBER="${OPENPETS_BUILD_NUMBER:-1}"
APP_NAME="OpenPets"
APP_BUNDLE="$ROOT/.build/release-package/$APP_NAME.app"
DMG_ROOT="$ROOT/.build/dmg-root"
DMG_PATH="$ROOT/.build/OpenPets-$VERSION.dmg"
IDENTITY="${DEVELOPER_ID_APPLICATION:-}"

rm -rf "$APP_BUNDLE" "$DMG_ROOT" "$DMG_PATH"
mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources" "$DMG_ROOT"

swift build -c release --product openpets-menubar --product openpets

cp "$ROOT/.build/release/openpets-menubar" "$APP_BUNDLE/Contents/MacOS/OpenPets"
cp "$ROOT/.build/release/openpets" "$APP_BUNDLE/Contents/MacOS/openpets"
cp "$ROOT/Packaging/OpenPets.app/Contents/Info.plist" "$APP_BUNDLE/Contents/Info.plist"

if [[ -n "${SPARKLE_PUBLIC_ED_KEY:-}" ]]; then
  /usr/libexec/PlistBuddy -c "Set :SUPublicEDKey $SPARKLE_PUBLIC_ED_KEY" "$APP_BUNDLE/Contents/Info.plist"
fi
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP_BUNDLE/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $BUILD_NUMBER" "$APP_BUNDLE/Contents/Info.plist"

if [[ -d "$ROOT/.build/release/OpenPets_OpenPetsCore.bundle" ]]; then
  cp -R "$ROOT/.build/release/OpenPets_OpenPetsCore.bundle" "$APP_BUNDLE/Contents/Resources/"
fi

if [[ -n "$IDENTITY" ]]; then
  codesign --force --options runtime --timestamp \
    --entitlements "$ROOT/Packaging/OpenPets.entitlements" \
    --sign "$IDENTITY" "$APP_BUNDLE"
fi

cp -R "$APP_BUNDLE" "$DMG_ROOT/"
ln -s /Applications "$DMG_ROOT/Applications"
hdiutil create -volname "OpenPets" -srcfolder "$DMG_ROOT" -ov -format UDZO "$DMG_PATH"

if [[ -n "$IDENTITY" ]]; then
  codesign --force --timestamp --sign "$IDENTITY" "$DMG_PATH"
fi

if [[ -n "${NOTARYTOOL_PROFILE:-}" ]]; then
  xcrun notarytool submit "$DMG_PATH" --keychain-profile "$NOTARYTOOL_PROFILE" --wait
  xcrun stapler staple "$DMG_PATH"
fi

shasum -a 256 "$DMG_PATH" > "$DMG_PATH.sha256"
echo "$DMG_PATH"
