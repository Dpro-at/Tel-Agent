#!/bin/sh
# Assemble a self-contained macOS payload for pkgbuild.
set -eu

ARCH="${1:?arm64 or amd64}"
STAGE="${2:?staging directory}"
PYTHON_BUILD="20250818"
PYTHON_VERSION="3.12.11"
NODE_VERSION="22.19.0"

case "$ARCH" in
  arm64) PY_ARCH="aarch64-apple-darwin"; NODE_ARCH="arm64" ;;
  amd64) PY_ARCH="x86_64-apple-darwin"; NODE_ARCH="x64" ;;
  *) echo "unsupported architecture: $ARCH" >&2; exit 1 ;;
esac

APP="$STAGE/root/Library/Tel-Agent"
mkdir -p "$APP" "$STAGE/scripts"
curl -fsSL -o /tmp/tel-agent-python.tar.gz "https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_BUILD}/cpython-${PYTHON_VERSION}+${PYTHON_BUILD}-${PY_ARCH}-install_only_stripped.tar.gz"
tar -xzf /tmp/tel-agent-python.tar.gz -C "$APP"
curl -fsSL -o /tmp/tel-agent-node.tar.gz "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-darwin-${NODE_ARCH}.tar.gz"
mkdir -p "$APP/node"
tar -xzf /tmp/tel-agent-node.tar.gz -C "$APP/node" --strip-components=1

"$APP/python/bin/python3" -m ensurepip --upgrade >/dev/null 2>&1 || true
"$APP/python/bin/python3" -m pip install --no-cache-dir .
cp -R api agent locales alembic "$APP/"
cp alembic.ini "$APP/"
cp packaging/macos/tel-agent.env "$APP/.env.template"
cp packaging/macos/api-start.sh "$APP/"
mkdir -p "$STAGE/root/Library/LaunchDaemons"
cp packaging/macos/com.dpro.tel-agent.api.plist "$STAGE/root/Library/LaunchDaemons/"
cp packaging/macos/com.dpro.tel-agent.web.plist "$STAGE/root/Library/LaunchDaemons/"
mkdir -p "$APP/web-app/web/.next/static"
cp -R web/.next/standalone/. "$APP/web-app/"
cp -R web/.next/static/. "$APP/web-app/web/.next/static/"
cp packaging/macos/postinstall "$STAGE/scripts/postinstall"
cp packaging/macos/preinstall "$STAGE/scripts/preinstall"
chmod 755 "$STAGE/scripts/"* "$APP/api-start.sh"
