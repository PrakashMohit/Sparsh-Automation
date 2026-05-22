#!/usr/bin/env bash
set -e

python --version
pip install -r requirement.txt

# On Render, skip install-deps (no root access) — base image has required libs
# Set cache dir explicitly so Playwright finds the browser
export PLAYWRIGHT_BROWSERS_PATH=/opt/render/.cache/ms-playwright
python -m playwright install chromium

echo "✅ Build complete"
