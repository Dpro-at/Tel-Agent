#!/bin/sh
# Assembles the file tree that becomes the deb and the rpm - run on a Linux CI
# runner, once per architecture.
#
# The package is deliberately self-contained: it carries its own CPython (from
# python-build-standalone) and its own Node, because "which Python does your distro
# ship" is exactly the support question a packaged product exists to remove. The
# cost is size; the win is that one artifact behaves the same on Debian, Ubuntu,
# Fedora and RHEL. Everything lands under /opt/tel-agent, and the venv is created
# at its final absolute path so it needs no relocation tricks.
#
# Usage: packaging/linux/build-tree.sh <amd64|arm64> <staging-dir>
# Expects the repository root as the working directory, with web/ already built
# (`npm run build` - the standalone bundle is what gets shipped).

set -eu

ARCH="${1:?arch: amd64 or arm64}"
STAGE="${2:?staging directory}"

# Release tags of the two bundled runtimes. Bump deliberately, with a live check.
PYTHON_BUILD="20250818"
PYTHON_VERSION="3.12.11"
NODE_VERSION="22.19.0"

case "$ARCH" in
  amd64) PY_ARCH="x86_64-unknown-linux-gnu"; NODE_ARCH="linux-x64" ;;
  arm64) PY_ARCH="aarch64-unknown-linux-gnu"; NODE_ARCH="linux-arm64" ;;
  *) echo "unsupported arch: $ARCH" >&2; exit 1 ;;
esac

OPT="$STAGE/opt/tel-agent"
mkdir -p "$OPT" "$STAGE/etc/tel-agent" "$STAGE/usr/lib/systemd/system"

# --- CPython ---------------------------------------------------------------
curl -fsSL -o /tmp/python.tar.gz \
  "https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_BUILD}/cpython-${PYTHON_VERSION}+${PYTHON_BUILD}-${PY_ARCH}-install_only.tar.gz"
tar -xzf /tmp/python.tar.gz -C "$OPT"   # the tarball's top level is python/

# --- Node (runs the dashboard's standalone server) -------------------------
curl -fsSL -o /tmp/node.tar.xz \
  "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-${NODE_ARCH}.tar.xz"
mkdir -p "$OPT/node"
tar -xJf /tmp/node.tar.xz -C "$OPT/node" --strip-components=1

# --- The API, straight into the bundled Python -----------------------------
# No venv, deliberately. A venv's symlinks and shebangs embed the absolute path
# of the interpreter that created it - built in a staging directory, they ship
# pointing at a runner path that exists on no target machine (the first proving
# run's exact failure). The bundled CPython is wholly ours, so its own
# site-packages IS the isolated environment; installing there leaves no
# symlinks and no baked-in paths to get wrong.
"$OPT/python/bin/python3" -m ensurepip --upgrade >/dev/null 2>&1 || true
"$OPT/python/bin/python3" -m pip install --no-cache-dir .

# The migration chain and its config ride along - the service migrates before it
# serves, same as the container entrypoint.
cp -r alembic "$OPT/alembic"
cp alembic.ini "$OPT/alembic.ini"

# --- The dashboard ---------------------------------------------------------
# outputFileTracingRoot is the repository, so the standalone bundle keeps the repo
# shape and the server entry is web/server.js under it.
mkdir -p "$OPT/web-app"
cp -r web/.next/standalone/. "$OPT/web-app/"
mkdir -p "$OPT/web-app/web/.next/static"
cp -r web/.next/static/. "$OPT/web-app/web/.next/static/"
# web/public does not exist in this repository today (assets ride the CDN); Next
# serves fine without it, so it is copied only if it ever appears.
if [ -d web/public ]; then
    cp -r web/public "$OPT/web-app/web/public"
fi

# --- Config template, services, launcher -----------------------------------
cp packaging/linux/tel-agent.env "$STAGE/etc/tel-agent/tel-agent.env"
cp packaging/linux/tel-agent-api.service "$STAGE/usr/lib/systemd/system/"
cp packaging/linux/tel-agent-web.service "$STAGE/usr/lib/systemd/system/"
cp packaging/linux/api-start.sh "$OPT/api-start.sh"

echo "staged $(du -sh "$STAGE" | cut -f1) for $ARCH"
