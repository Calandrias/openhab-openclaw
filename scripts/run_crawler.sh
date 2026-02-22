#!/bin/bash
set -e

# Adjust this if you clone to a different path
REPO_DIR="/home/node/openhab-openclaw"

cd "$REPO_DIR"

# Ensure venv exists (created once manually or via a separate setup step)
VENV_PY="$REPO_DIR/.venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
  echo "Virtualenv Python not found at $VENV_PY"
  exit 1
fi

"$VENV_PY" scripts/openhab_crawler.py >> "$REPO_DIR/openhab_crawler.log" 2>&1
