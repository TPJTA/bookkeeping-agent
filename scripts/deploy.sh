#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/bookkeeping-agent}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="${SERVICE_NAME:-bookkeeping-agent}"

echo "Deploying ${BRANCH} in ${PROJECT_DIR}"

cd "$PROJECT_DIR"

git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

if command -v uv >/dev/null 2>&1; then
  UV_BIN="uv"
elif [ -x "$HOME/.local/bin/uv" ]; then
  UV_BIN="$HOME/.local/bin/uv"
else
  echo "uv is not installed. Install it on the server first: https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi

"$UV_BIN" sync --frozen

sudo systemctl restart "$SERVICE_NAME"
sudo systemctl --no-pager --full status "$SERVICE_NAME"
