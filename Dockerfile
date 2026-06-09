# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.13

# Build stage uses Astral's official uv + python image
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-trixie-slim AS build
WORKDIR /app
# Global env var (not _FOR_<dist>) because hatch-vcs calls setuptools-scm without dist_name.
ARG SETUPTOOLS_SCM_PRETEND_VERSION
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${SETUPTOOLS_SCM_PRETEND_VERSION}
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ src/
RUN uv build --wheel

# Runtime stage stays minimal — slim Python, pip-install the wheel
FROM python:${PYTHON_VERSION}-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl
ENV FABRIC_AUTH=default PYTHONUNBUFFERED=1
ENTRYPOINT ["fabric-dw-mcp"]
