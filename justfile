default: check

lint:
    uv run ruff check .
    uv run ruff format --check .

fmt:
    uv run ruff format .

fix:
    uv run ruff check --fix .
    uv run ruff format .

type:
    uv run ty check src tests

test:
    uv run pytest tests/unit -q

cov:
    uv run pytest tests/unit --cov --cov-report=term
    uv run coverage report --fail-under=80

cov-html:
    uv run pytest tests/unit --cov --cov-report=html
    uv run coverage report --fail-under=80

slow:
    uv run pytest tests/unit -m slow -v

integration:
    uv run pytest tests/integration -q -m "integration and not sql_endpoint"

build:
    uv build

# Shared calver validator — used by both 'release' and 'tag'.
# Accepts YYYY.M.N where year is 20xx, month is 1–12 (no leading zero), patch is 0 or
# any positive integer without a leading zero.  Rejects prerelease suffixes (aN/bN/rcN/.devN).
# Single source of truth: update this regex in one place only.
_CALVER_RE := '^20[0-9]{2}\.([1-9]|1[0-2])\.(0|[1-9][0-9]*)$'

# Bump plugin.json to VERSION (must be a stable calver like 2026.6.1; no prerelease suffixes).
# Run: just release VERSION  →  open a release-prep PR  →  merge  →  just tag VERSION
release VERSION:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! echo "{{ VERSION }}" | grep -qE '{{ _CALVER_RE }}'; then
        echo "error: '{{ VERSION }}' is not a stable calver (expected YYYY.M.N, month 1–12, no leading zeros, no prerelease suffix)" >&2
        exit 1
    fi
    plugin_json=".claude-plugin/plugin.json"
    # Replace only the version value; keep key order, indentation, and trailing newline byte-identical.
    # sed -i '' on macOS; sed -i on Linux — detect via uname.
    if [ "$(uname)" = "Darwin" ]; then
        sed -i '' 's/"version": "[^"]*"/"version": "{{ VERSION }}"/' "$plugin_json"
    else
        sed -i 's/"version": "[^"]*"/"version": "{{ VERSION }}"/' "$plugin_json"
    fi
    echo "plugin.json version set to {{ VERSION }}"
    echo ""
    echo "Next steps:"
    echo "  1. git add .claude-plugin/plugin.json && git commit -m 'chore: release {{ VERSION }}'"
    echo "  2. Open a release-prep PR and merge it to main."
    echo "  3. After merge: just tag {{ VERSION }}"

# Assert plugin.json version (from the committed tree) matches VERSION, then create and push an
# annotated git tag.  Refuses to tag on a dirty working tree, a non-main branch, or when the
# branch is behind origin/main.
# Run after the release-prep PR (from 'just release VERSION') has been merged to main.
tag VERSION:
    #!/usr/bin/env bash
    set -euo pipefail
    # Validate VERSION with the same tightened calver check used by 'release'.
    if ! echo "{{ VERSION }}" | grep -qE '{{ _CALVER_RE }}'; then
        echo "error: '{{ VERSION }}' is not a stable calver (expected YYYY.M.N, month 1–12, no leading zeros, no prerelease suffix)" >&2
        exit 1
    fi
    # Guard: working tree must be clean.
    if ! git diff --quiet || ! git diff --cached --quiet; then
        echo "error: working tree is dirty — commit or stash changes before tagging" >&2
        exit 1
    fi
    # Guard: must be on main.
    current_branch=$(git rev-parse --abbrev-ref HEAD)
    if [ "$current_branch" != "main" ]; then
        echo "error: must be on 'main' to tag (currently on '$current_branch')" >&2
        exit 1
    fi
    # Guard: branch must be up to date with origin/main.
    git fetch origin main --quiet
    local_sha=$(git rev-parse HEAD)
    remote_sha=$(git rev-parse origin/main)
    if [ "$local_sha" != "$remote_sha" ]; then
        echo "error: local main is not up to date with origin/main — run 'git pull' first" >&2
        exit 1
    fi
    # Read version from the committed tree, not the working tree.
    plugin_version=$(git show HEAD:.claude-plugin/plugin.json | python3 -c "import json,sys; print(json.load(sys.stdin)['version'])")
    if [ "$plugin_version" != "{{ VERSION }}" ]; then
        echo "error: plugin.json version in HEAD ('$plugin_version') does not match '{{ VERSION }}'" >&2
        echo "       Run 'just release {{ VERSION }}', open a PR, merge it, then retry 'just tag {{ VERSION }}'." >&2
        exit 1
    fi
    git tag -a "v{{ VERSION }}" -m "Release v{{ VERSION }}"
    git push origin "v{{ VERSION }}"
    echo "Tagged and pushed v{{ VERSION }}"

audit:
    uvx 'bandit[sarif]==1.9.4' -r src/ -ll
    uvx 'pip-audit==2.10.1' --strict

check: lint type test
