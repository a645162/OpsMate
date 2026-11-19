#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --extra gui --extra build
uv run python packaging/build.py --all
