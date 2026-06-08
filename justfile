default: check

lint:
    uv run ruff check .
    uv run ruff format --check .

fmt:
    uv run ruff format .

type:
    uvx ty==0.0.44 check src tests

test:
    uv run pytest tests/unit -q

cov:
    uv run pytest tests/unit --cov=fabric_dw --cov-report=term

check: lint type test
