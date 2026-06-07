#!/usr/bin/env bash
set -euo pipefail
uv run --only-group docs zensical build
curl -sLo ./docs_build/site/t.js "https://cloud.umami.is/script.js"
