#!/usr/bin/env bash
# Launcher: runs the Python bootstrap (clone). No PowerShell needed.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
py="$(command -v python3 || command -v python || true)"
if [ -z "$py" ]; then
  echo "Python 3 is required (apt install python3)." >&2
  exit 1
fi

PYTHONPATH="$script_dir${PYTHONPATH:+:$PYTHONPATH}" exec "$py" -m bootstrap clone "$@"
