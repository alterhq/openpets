#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${OPENPETS_VERSION:-0.1.0}"
BUILD_NUMBER="${OPENPETS_BUILD_NUMBER:-1}"
RELEASE_TAG="${OPENPETS_RELEASE_TAG:-v$VERSION}"
APP_NAME="OpenPets"
APP_BUNDLE="$ROOT/.build/release-package/$APP_NAME.app"
APP_EXECUTABLE="$APP_BUNDLE/Contents/MacOS/OpenPets"
BUNDLED_CLI_EXECUTABLE="$APP_BUNDLE/Contents/MacOS/openpets-cli"
RELEASE_DIR="$ROOT/.build/release-artifacts"
APPCAST_DIR="$ROOT/.build/sparkle-updates"
DMG_ROOT="$ROOT/.build/dmg-root"
DMG_PATH="$RELEASE_DIR/OpenPets-$VERSION.dmg"
ZIP_PATH="$APPCAST_DIR/OpenPets-$VERSION.zip"
APPCAST_PATH="$APPCAST_DIR/appcast.xml"
ARM64_BUILD_DIR="$ROOT/.build/release-arm64"
X86_64_BUILD_DIR="$ROOT/.build/release-x86_64"
ARM64_RELEASE_DIR="$ARM64_BUILD_DIR/arm64-apple-macosx/release"
X86_64_RELEASE_DIR="$X86_64_BUILD_DIR/x86_64-apple-macosx/release"
IDENTITY="${CODESIGN_IDENTITY:-${DEVELOPER_ID_APPLICATION:-}}"
NOTARY_PROFILE="${NOTARYTOOL_PROFILE:-}"
APPLE_ID="${APPLE_ID:-}"
TEAM_ID="${TEAM_ID:-}"
NOTARIZATION_PASSWORD="${NOTARIZATION_PASSWORD:-}"
SPARKLE_FEED_URL="${OPENPETS_SPARKLE_FEED_URL:-https://github.com/alterhq/openpets/releases/latest/download/appcast.xml}"
DOWNLOAD_URL_PREFIX="${OPENPETS_RELEASE_DOWNLOAD_URL_PREFIX:-https://github.com/alterhq/openpets/releases/download/$RELEASE_TAG/}"
SPARKLE_PUBLIC_KEY_PLACEHOLDER="REPLACE_WITH_SPARKLE_EDDSA_PUBLIC_KEY"

build_product() {
  local triple="$1"
  local scratch_path="$2"
  local product="$3"

  swift build \
    -c release \
    --triple "$triple" \
    --scratch-path "$scratch_path" \
    --product "$product" \
    -Xlinker -rpath \
    -Xlinker @loader_path/../Frameworks
}

sign_item() {
  local path="$1"
  shift

  if [[ -e "$path" ]]; then
    codesign --force --timestamp --options runtime --sign "$IDENTITY" "$@" "$path"
  fi
}

notarize_file() {
  local path="$1"

  if [[ -n "$NOTARY_PROFILE" ]]; then
    xcrun notarytool submit "$path" --keychain-profile "$NOTARY_PROFILE" --wait
    return
  fi

  if [[ -n "$APPLE_ID" && -n "$TEAM_ID" && -n "$NOTARIZATION_PASSWORD" ]]; then
    xcrun notarytool submit "$path" \
      --apple-id "$APPLE_ID" \
      --team-id "$TEAM_ID" \
      --password "$NOTARIZATION_PASSWORD" \
      --wait
  fi
}

has_notary_credentials() {
  [[ -n "$NOTARY_PROFILE" ]] || [[ -n "$APPLE_ID" && -n "$TEAM_ID" && -n "$NOTARIZATION_PASSWORD" ]]
}

release_notes_path() {
  local notes_path="$APPCAST_DIR/OpenPets-$VERSION.md"

  if [[ -n "${OPENPETS_RELEASE_NOTES_FILE:-}" && -f "$OPENPETS_RELEASE_NOTES_FILE" ]]; then
    cp "$OPENPETS_RELEASE_NOTES_FILE" "$notes_path"
  elif [[ -n "${OPENPETS_RELEASE_NOTES:-}" ]]; then
    printf '%s\n' "$OPENPETS_RELEASE_NOTES" > "$notes_path"
  fi
}

rm -rf "$APP_BUNDLE" "$DMG_ROOT" "$DMG_PATH" "$ZIP_PATH" "$APPCAST_PATH"
mkdir -p \
  "$APP_BUNDLE/Contents/MacOS" \
  "$APP_BUNDLE/Contents/Resources" \
  "$APP_BUNDLE/Contents/Frameworks" \
  "$DMG_ROOT" \
  "$RELEASE_DIR" \
  "$APPCAST_DIR"

if [[ -n "${SPARKLE_PRIVATE_KEY:-}" ]]; then
  if [[ -z "${SPARKLE_PUBLIC_ED_KEY:-}" || "$SPARKLE_PUBLIC_ED_KEY" == "$SPARKLE_PUBLIC_KEY_PLACEHOLDER" ]]; then
    echo "SPARKLE_PUBLIC_ED_KEY must be set when generating a signed Sparkle appcast." >&2
    exit 1
  fi
fi

build_product "arm64-apple-macosx14.0" "$ARM64_BUILD_DIR" "openpets"
build_product "arm64-apple-macosx14.0" "$ARM64_BUILD_DIR" "openpets-menubar"
build_product "x86_64-apple-macosx14.0" "$X86_64_BUILD_DIR" "openpets"
build_product "x86_64-apple-macosx14.0" "$X86_64_BUILD_DIR" "openpets-menubar"

lipo -create \
  "$ARM64_RELEASE_DIR/openpets-menubar" \
  "$X86_64_RELEASE_DIR/openpets-menubar" \
  -output "$APP_EXECUTABLE"
lipo -create \
  "$ARM64_RELEASE_DIR/openpets" \
  "$X86_64_RELEASE_DIR/openpets" \
  -output "$BUNDLED_CLI_EXECUTABLE"
chmod +x "$APP_EXECUTABLE" "$BUNDLED_CLI_EXECUTABLE"

if [[ "$(stat -f '%i' "$APP_EXECUTABLE")" == "$(stat -f '%i' "$BUNDLED_CLI_EXECUTABLE")" ]]; then
  echo "Packaged app executable and CLI executable resolve to the same file." >&2
  exit 1
fi

if ! otool -L "$APP_EXECUTABLE" | grep -q "Sparkle.framework"; then
  echo "Packaged app executable does not look like the menu bar app." >&2
  exit 1
fi

if otool -L "$BUNDLED_CLI_EXECUTABLE" | grep -q "Sparkle.framework"; then
  echo "Packaged CLI executable does not look like the CLI." >&2
  exit 1
fi

cp "$ROOT/Packaging/OpenPets.app/Contents/Info.plist" "$APP_BUNDLE/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP_BUNDLE/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $BUILD_NUMBER" "$APP_BUNDLE/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :SUFeedURL $SPARKLE_FEED_URL" "$APP_BUNDLE/Contents/Info.plist"
PACKAGED_BUNDLE_EXECUTABLE="$(/usr/libexec/PlistBuddy -c "Print :CFBundleExecutable" "$APP_BUNDLE/Contents/Info.plist")"
if [[ "$PACKAGED_BUNDLE_EXECUTABLE" != "OpenPets" ]]; then
  echo "CFBundleExecutable must point at the OpenPets menu bar executable." >&2
  exit 1
fi
if [[ -n "${SPARKLE_PUBLIC_ED_KEY:-}" ]]; then
  /usr/libexec/PlistBuddy -c "Set :SUPublicEDKey $SPARKLE_PUBLIC_ED_KEY" "$APP_BUNDLE/Contents/Info.plist"
fi
if [[ -n "${SPARKLE_PRIVATE_KEY:-}" ]]; then
  PACKAGED_SPARKLE_PUBLIC_KEY="$(/usr/libexec/PlistBuddy -c "Print :SUPublicEDKey" "$APP_BUNDLE/Contents/Info.plist")"
  if [[ "$PACKAGED_SPARKLE_PUBLIC_KEY" == "$SPARKLE_PUBLIC_KEY_PLACEHOLDER" ]]; then
    echo "Packaged SUPublicEDKey still contains the placeholder value." >&2
    exit 1
  fi
fi

cp -R "$ARM64_RELEASE_DIR/Sparkle.framework" "$APP_BUNDLE/Contents/Frameworks/"

if [[ -d "$ARM64_RELEASE_DIR/OpenPets_OpenPetsCore.bundle" ]]; then
  cp -R "$ARM64_RELEASE_DIR/OpenPets_OpenPetsCore.bundle" "$APP_BUNDLE/Contents/Resources/"
fi

if [[ -n "$IDENTITY" ]]; then
  SPARKLE_FRAMEWORK="$APP_BUNDLE/Contents/Frameworks/Sparkle.framework"
  sign_item "$SPARKLE_FRAMEWORK/Versions/B/Autoupdate" --preserve-metadata=identifier,entitlements,flags --generate-entitlement-der
  sign_item "$SPARKLE_FRAMEWORK/Versions/B/Sparkle" --preserve-metadata=identifier,entitlements,flags --generate-entitlement-der
  sign_item "$SPARKLE_FRAMEWORK/Versions/B/Updater.app" --preserve-metadata=identifier,entitlements,flags --generate-entitlement-der
  sign_item "$SPARKLE_FRAMEWORK/Versions/B/XPCServices/Downloader.xpc" --preserve-metadata=identifier,entitlements,flags --generate-entitlement-der
  sign_item "$SPARKLE_FRAMEWORK/Versions/B/XPCServices/Installer.xpc" --preserve-metadata=identifier,entitlements,flags --generate-entitlement-der
  sign_item "$SPARKLE_FRAMEWORK" --preserve-metadata=identifier,entitlements,flags --generate-entitlement-der
  sign_item "$BUNDLED_CLI_EXECUTABLE"
  sign_item "$APP_BUNDLE" --entitlements "$ROOT/Packaging/OpenPets.entitlements"
  codesign --verify --strict --verbose=4 "$BUNDLED_CLI_EXECUTABLE"
  codesign --verify --strict --verbose=4 "$APP_BUNDLE"
fi

ditto -c -k --keepParent --norsrc --noextattr --noqtn "$APP_BUNDLE" "$ZIP_PATH"

if [[ -n "$IDENTITY" ]] && has_notary_credentials; then
  notarize_file "$ZIP_PATH"
  xcrun stapler staple "$APP_BUNDLE"
  ditto -c -k --keepParent --norsrc --noextattr --noqtn "$APP_BUNDLE" "$ZIP_PATH"
fi

cp -R "$APP_BUNDLE" "$DMG_ROOT/"
ln -s /Applications "$DMG_ROOT/Applications"
hdiutil create -volname "OpenPets" -srcfolder "$DMG_ROOT" -ov -format UDZO "$DMG_PATH"

if [[ -n "$IDENTITY" ]]; then
  codesign --force --timestamp --sign "$IDENTITY" "$DMG_PATH"
fi

if [[ -n "$IDENTITY" ]] && has_notary_credentials; then
  notarize_file "$DMG_PATH"
  xcrun stapler staple "$DMG_PATH"
fi

release_notes_path
if [[ -n "${SPARKLE_PRIVATE_KEY:-}" ]]; then
  echo "$SPARKLE_PRIVATE_KEY" | "$ARM64_BUILD_DIR/artifacts/sparkle/Sparkle/bin/generate_appcast" \
    --ed-key-file - \
    --download-url-prefix "$DOWNLOAD_URL_PREFIX" \
    --link "https://github.com/alterhq/openpets" \
    "$APPCAST_DIR"
elif [[ "${OPENPETS_GENERATE_UNSIGNED_APPCAST:-0}" == "1" ]]; then
  "$ARM64_BUILD_DIR/artifacts/sparkle/Sparkle/bin/generate_appcast" \
    --download-url-prefix "$DOWNLOAD_URL_PREFIX" \
    --link "https://github.com/alterhq/openpets" \
    "$APPCAST_DIR"
fi

cp "$ZIP_PATH" "$RELEASE_DIR/"
if [[ -f "$APPCAST_PATH" ]]; then
  cp "$APPCAST_PATH" "$RELEASE_DIR/appcast.xml"
fi
(
  cd "$RELEASE_DIR"
  shasum -a 256 "OpenPets-$VERSION.dmg" > "OpenPets-$VERSION.dmg.sha256"
  shasum -a 256 "OpenPets-$VERSION.zip" > "OpenPets-$VERSION.zip.sha256"
)

echo "$RELEASE_DIR"
