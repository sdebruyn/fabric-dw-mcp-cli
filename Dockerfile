# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.14

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
# Build the wheel, then install all runtime deps from the frozen lock into a venv.
# uv sync --frozen reads uv.lock and installs exactly the locked versions (no fresh resolution).
RUN uv build --wheel && \
    uv sync --frozen --no-dev --no-install-project && \
    uv pip install --no-deps dist/*.whl

# Runtime stage — copy the pre-built venv from the build stage (all deps already pinned by lock).
# Use plain python slim (no uv binary in production image) — Python path matches the build stage.
FROM python:${PYTHON_VERSION}-slim AS runtime

ARG VERSION
# OCI image labels
LABEL org.opencontainers.image.title="fabric-dw" \
      org.opencontainers.image.description="Python CLI + MCP server for administering Microsoft Fabric Data Warehouses and SQL Analytics Endpoints" \
      org.opencontainers.image.url="https://fdw.debruyn.dev" \
      org.opencontainers.image.source="https://github.com/sdebruyn/fabric-dw-mcp-cli" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="MIT"

# MCP Registry ownership verification for the "oci" package type in server.json: the
# registry checks this annotation against server.json's `name`, not identifier-matching
# (unlike PyPI/NuGet/cargo, which check an `mcp-name:` string in the package README).
LABEL io.modelcontextprotocol.server.name="io.github.sdebruyn/fabric-dw-mcp"

# Install system dependencies using BuildKit cache mount for faster rebuilds.
# ca-certificates: required for TLS connections to Fabric REST APIs and Azure AD.
# mssql-python (the SQL driver) bundles its own native driver (no separate ODBC
# manager needed), so no unixodbc/libodbc system packages are required on top of
# the glibc/OpenSSL already present in the Debian trixie-slim base image.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends ca-certificates

# Copy the virtualenv (with all locked deps + the wheel) from the build stage.
# No pip/uv resolution happens here — all versions are already pinned by uv.lock.
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

ENV FABRIC_AUTH=default PYTHONUNBUFFERED=1

# Run as non-root for security hardening; create home so Path.home() resolves correctly
RUN useradd --uid 10001 --create-home --home-dir /home/app --shell /usr/sbin/nologin app
ENV HOME=/home/app
USER app

# HEALTHCHECK is intentionally omitted: fabric-dw-mcp is a stdio MCP server that
# communicates over stdin/stdout, not a long-running HTTP/TCP service. There is no
# socket or port to probe.
ENTRYPOINT ["fabric-dw-mcp"]
