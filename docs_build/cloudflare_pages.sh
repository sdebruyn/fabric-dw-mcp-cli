#!/usr/bin/env bash
set -euo pipefail

# Required CF dashboard env vars (Settings → Environment variables):
#   SKIP_DEPENDENCY_INSTALL=1   — prevents CF from running `pip install .`,
#                                 which would pull in all runtime deps the docs
#                                 build doesn't need (~17 s saved).
#   PYTHON_VERSION=3.13.3       — pins to the version pre-cached in CF's V3
#                                 build image so pyenv never compiles from source
#                                 (~95 s saved).  The repo's .python-version
#                                 (3.13) is still read by local uv/pyenv for dev.
pip install --quiet --upgrade "zensical>=0.0.42"

zensical build
curl -sLo ./docs_build/site/t.js "https://cloud.umami.is/script.js"
