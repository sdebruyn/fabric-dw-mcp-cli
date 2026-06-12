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

cov-html:
    uv run pytest tests/unit --cov --cov-report=html

integration:
    uv run pytest tests/integration -q -m integration

build:
    uv build

audit:
    uvx 'bandit[sarif]' -r src/ -ll
    uvx pip-audit --strict

check: lint type test
