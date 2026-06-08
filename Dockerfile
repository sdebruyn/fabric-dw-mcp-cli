# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.13

# Build stage uses Astral's official uv + python image
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-trixie-slim AS build
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ src/
COPY .git .git
RUN uv sync --frozen --no-dev && uv build --wheel && rm -rf .git

# Runtime stage stays minimal — slim Python, pip-install the wheel
FROM python:${PYTHON_VERSION}-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl
ENV FABRIC_AUTH=default PYTHONUNBUFFERED=1
ENTRYPOINT ["fabric-dw-mcp"]
