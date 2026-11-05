#!/usr/bin/env bash
#
# Build the OpenPets Linux .deb package (Architecture: all).
#
# Usage: ./scripts/package-linux.sh VERSION OUTPUT_DIRECTORY
#
# VERSION        Debian package version (semver, optional leading "v").
#                Passed explicitly by the caller; never derived from git tags.
# OUTPUT_DIRECTORY   Directory receiving openpets_<VERSION>_all.deb
#                    (created if missing; an existing .deb is not overwritten).
#
# The packager stages files under a mktemp directory scoped by an EXIT trap
# and builds with "dpkg-deb --root-owner-group", so it never needs root and
# never installs anything on the host. OpenPets Linux sources must already be
# present at Linux/openpets_linux (overridable with OPENPETS_LINUX_SOURCES).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $EUID -eq 0 ]]; then
    echo "Refusing to build as root: package as an unprivileged user." >&2
    exit 1
fi

if ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "dpkg-deb is required to build the .deb package." >&2
    exit 1
fi

if [[ $# -ne 2 ]]; then
    echo "Usage: $0 VERSION OUTPUT_DIRECTORY" >&2
    exit 1
fi

RAW_VERSION="$1"
OUTPUT_DIR="$2"

# Strip an optional leading "v" (callers may pass tags); versions are never
# read from git tags.
VERSION="${RAW_VERSION#v}"

is_valid_version() {
    local v="$1"
    [[ "$v" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.+-]+(\.[0-9A-Za-z.+-]+)*)?(\+[0-9A-Za-z.+-]+(\.[0-9A-Za-z.+-]+)*)?$ ]]
}

if [[ -z "$VERSION" ]] || ! is_valid_version "$VERSION"; then
    echo "Invalid version: $RAW_VERSION (expected semver, e.g. 1.2.3)" >&2
    exit 1
fi

if [[ -e "$OUTPUT_DIR" && ! -d "$OUTPUT_DIR" ]]; then
    echo "Output path exists but is not a directory: $OUTPUT_DIR" >&2
    exit 1
fi
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"

SOURCES_DIR="${OPENPETS_LINUX_SOURCES:-$ROOT/Linux/openpets_linux}"
if [[ ! -d "$SOURCES_DIR" ]]; then
    echo "OpenPets Linux sources not found: $SOURCES_DIR" >&2
    echo "Set OPENPETS_LINUX_SOURCES to the openpets_linux package directory." >&2
    exit 1
fi
if [[ -L "$SOURCES_DIR" ]] || [[ -n "$(find "$SOURCES_DIR" ! -type d ! -type f -print -quit)" ]]; then
    echo "Package sources must contain only regular files and directories (no symlinks)." >&2
    exit 1
fi

DESKTOP_SRC="$ROOT/Linux/packaging/openpets.desktop"
if [[ ! -f "$DESKTOP_SRC" ]]; then
    echo "Desktop entry not found: $DESKTOP_SRC" >&2
    exit 1
fi

LICENSE="$ROOT/LICENSE"
if [[ ! -f "$LICENSE" ]]; then
    echo "LICENSE not found: $LICENSE" >&2
    exit 1
fi

PACKAGE_NAME="openpets"
DEB_NAME="openpets_${VERSION}_all.deb"

# Refuse to silently overwrite an existing release artifact.
DEB_PATH="$OUTPUT_DIR/$DEB_NAME"
if [[ -e "$DEB_PATH" || -L "$DEB_PATH" ]]; then
    echo "Refusing to overwrite existing package: $DEB_PATH" >&2
    exit 1
fi

# Scoped staging area, removed on exit (success or failure).
STAGING_DIR="$(mktemp -d -t openpets-linux-packaging.XXXXXX)"
trap 'rm -rf "$STAGING_DIR"' EXIT

STAGE_USR="$STAGING_DIR/usr"
mkdir -p \
    "$STAGING_DIR/DEBIAN" \
    "$STAGE_USR/bin" \
    "$STAGE_USR/lib/$PACKAGE_NAME" \
    "$STAGE_USR/share/applications" \
    "$STAGE_USR/share/doc/$PACKAGE_NAME"

# --- usr/bin/openpets wrapper -------------------------------------------
# Isolate the installed entry point from the current directory and PYTHONPATH.
cat > "$STAGE_USR/bin/openpets" <<'WRAPPER'
#!/bin/sh
# OpenPets Linux launcher.
exec /usr/bin/python3 -I -c 'import runpy, sys; sys.path.insert(0, "/usr/lib/openpets"); runpy.run_module("openpets_linux", run_name="__main__")' "$@"
WRAPPER
chmod 0755 "$STAGE_USR/bin/openpets"

# --- usr/lib/openpets/openpets_linux (Python package) -------------------
# Copy package sources, then drop bytecode caches so __pycache__ never ships.
cp -a "$SOURCES_DIR/." "$STAGE_USR/lib/$PACKAGE_NAME/openpets_linux/"
cp "$ROOT/Linux/requirements-mcp.txt" "$STAGE_USR/lib/$PACKAGE_NAME/requirements-mcp.txt"
chmod 0644 "$STAGE_USR/lib/$PACKAGE_NAME/requirements-mcp.txt"
find "$STAGE_USR/lib/$PACKAGE_NAME/openpets_linux" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$STAGE_USR/lib/$PACKAGE_NAME/openpets_linux" -type d -exec chmod 0755 {} +
find "$STAGE_USR/lib/$PACKAGE_NAME/openpets_linux" -type f -exec chmod 0644 {} +

# --- usr/share/applications/openpets.desktop ------------------------------
cp "$DESKTOP_SRC" "$STAGE_USR/share/applications/openpets.desktop"
chmod 0644 "$STAGE_USR/share/applications/openpets.desktop"

# --- usr/share/doc/openpets/copyright -------------------------------------
cp "$LICENSE" "$STAGE_USR/share/doc/$PACKAGE_NAME/copyright"
chmod 0644 "$STAGE_USR/share/doc/$PACKAGE_NAME/copyright"

# --- DEBIAN/control -------------------------------------------------------
cat > "$STAGING_DIR/DEBIAN/control" <<CONTROL
Package: $PACKAGE_NAME
Version: $VERSION
Section: x11
Priority: optional
Architecture: all
Maintainer: OpenPets contributors <openpets@noreply.alterhq.github>
Depends: python3 (>= 3.10), python3-venv, python3-gi, python3-gi-cairo, gir1.2-gtk-3.0, gir1.2-ayatanaappindicator3-0.1, webp-pixbuf-loader
Description: OpenPets desktop companion for Linux
 Lightweight GTK3 desktop pet companion for same-user local use on Linux.
 Provides the /usr/bin/openpets launcher and the python3 -m openpets_linux
  entry point, bundled Starcorn pet, notification actions, and opt-in plugins.
  MCP is optional: install its SDK in a user venv with "openpets setup-mcp".
Homepage: https://github.com/alterhq/openpets
CONTROL
chmod 0644 "$STAGING_DIR/DEBIAN/control"

# Build without root: --root-owner-group records root ownership in the archive.
dpkg-deb --root-owner-group -b "$STAGING_DIR" "$DEB_PATH" >/dev/null

echo "$DEB_PATH"
