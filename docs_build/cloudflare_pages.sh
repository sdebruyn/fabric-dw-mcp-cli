#!/usr/bin/env bash
set -euo pipefail

# Cloudflare Pages already runs `pip install .` on this project; we only need
# zensical itself to build the docs site.
pip install --quiet --upgrade "zensical>=0.0.42"

zensical build
curl -sLo ./docs_build/site/t.js "https://cloud.umami.is/script.js"
