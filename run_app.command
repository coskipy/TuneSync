#!/bin/zsh
set -euo pipefail
cd "${0:A:h}"
exec "./.venv/bin/python" -m lightsync_app
