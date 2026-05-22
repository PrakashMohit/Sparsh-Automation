#!/usr/bin/env bash
set -e
python --version
pip install -r requirement.txt
echo "✅ Build complete — browser will be installed at runtime"
