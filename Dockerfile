# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.13

# --- Build stage ---
FROM python:${PYTHON_VERSION}-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"
WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ src/
RUN uv sync --frozen --no-dev
RUN uv build --wheel

# --- Runtime stage ---
FROM python:${PYTHON_VERSION}-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

ENV FABRIC_AUTH=default \
    PYTHONUNBUFFERED=1
ENTRYPOINT ["fabric-dw-mcp"]
