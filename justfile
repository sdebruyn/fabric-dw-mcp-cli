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

# Bump plugin.json to VERSION (must be a stable calver like 2026.6.0; no prerelease suffixes).
# Run: just release VERSION  →  open a release-prep PR  →  merge  →  just tag VERSION
release VERSION:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! echo "{{ VERSION }}" | grep -qE '^[0-9]{4}\.[0-9]{1,2}\.[0-9]+$'; then
        echo "error: '{{ VERSION }}' is not a stable calver (expected YYYY.M.N with no prerelease suffix)" >&2
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
    echo "  1. git add .claude-plugin/plugin.json && git commit -m 'build: release {{ VERSION }}'"
    echo "  2. Open a release-prep PR and merge it to main."
    echo "  3. After merge: just tag {{ VERSION }}"

# Assert plugin.json version matches VERSION, then create and push an annotated git tag.
# Run after the release-prep PR (from 'just release VERSION') has been merged to main.
tag VERSION:
    #!/usr/bin/env bash
    set -euo pipefail
    plugin_version=$(python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json'))['version'])")
    if [ "$plugin_version" != "{{ VERSION }}" ]; then
        echo "error: plugin.json version ('$plugin_version') does not match '{{ VERSION }}'" >&2
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
