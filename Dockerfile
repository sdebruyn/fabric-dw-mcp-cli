# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.13

# Build stage uses Astral's official uv + python image
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-trixie-slim AS build
WORKDIR /app

# VERSION must be supplied via --build-arg; hatch-vcs cannot see .git in the Docker context
# (the .dockerignore excludes .git intentionally to keep the context small).
ARG VERSION
RUN test -n "$VERSION" || (echo "ERROR: pass --build-arg VERSION=x.y.z (hatch-vcs cannot see .git in the Docker context)" && exit 1)

# Global env var (not _FOR_<dist>) because hatch-vcs calls setuptools-scm without dist_name.
# SETUPTOOLS_SCM_PRETEND_VERSION_FOR_FABRIC_DW is the dist-specific override for package 'fabric-dw'.
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION}
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_FABRIC_DW=${VERSION}

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ src/
RUN uv build --wheel

# Runtime stage stays minimal — slim Python, pip-install the wheel
FROM python:${PYTHON_VERSION}-slim AS runtime

ARG VERSION
# OCI image labels
LABEL org.opencontainers.image.title="fabric-dw" \
      org.opencontainers.image.description="Python CLI + MCP server for administering Microsoft Fabric Data Warehouses and SQL Analytics Endpoints" \
      org.opencontainers.image.url="https://fdw.debruyn.dev" \
      org.opencontainers.image.source="https://github.com/sdebruyn/fabric-dw-mcp-cli" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="MIT"

# Install system dependencies using BuildKit cache mount for faster rebuilds
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends ca-certificates

COPY --from=build /app/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

ENV FABRIC_AUTH=default PYTHONUNBUFFERED=1

# Run as non-root for security hardening
RUN useradd --uid 10001 --no-create-home --shell /sbin/nologin app
USER app

# HEALTHCHECK is intentionally omitted: fabric-dw-mcp is a stdio MCP server that
# communicates over stdin/stdout, not a long-running HTTP/TCP service. There is no
# socket or port to probe.
ENTRYPOINT ["fabric-dw-mcp"]
