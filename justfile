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

audit:
    uvx 'bandit[sarif]==1.9.4' -r src/ -ll
    uvx 'pip-audit==2.10.1' --strict

check: lint type test
