#!/usr/bin/env bash
# build.sh — runs once on Render during deploy
set -e
pip install -r requirements.txt
python -m playwright install chromium
python -m playwright install-deps chromium
echo "✅ Build complete"