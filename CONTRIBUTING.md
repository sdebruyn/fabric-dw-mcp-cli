# Contributing

Thank you for your interest in contributing to `fabric-dw-mcp-cli`!

## Dev Setup

> **Recommended:** open the repo in [GitHub Codespaces](https://codespaces.new/sdebruyn/fabric-dw-mcp-cli) or VS Code with the Remote-Containers extension — the devcontainer handles all of steps 1–2 automatically.

1. **Install dependencies**

   ```bash
   uv sync
   ```

2. **Install [just](https://github.com/casey/just#installation)** (optional, for convenient local checks)

3. **Authenticate with Azure** (required for integration tests)

   ```bash
   az login
   ```

## Branch Flow

- Branch off `main` for every change.
- Use descriptive branch names, e.g. `feat/add-table-command` or `fix/connection-timeout`.
- Open a pull request back to `main`.
- PR titles must follow [Conventional Commits](#conventional-commits) — they become the squash-merge commit message.
- All PRs are squash-merged.

## Conventional Commits

PR titles (and therefore merge commits) must use one of these types:

| Type       | When to use                                      |
| ---------- | ------------------------------------------------ |
| `feat`     | New user-facing feature                          |
| `fix`      | Bug fix                                          |
| `chore`    | Maintenance, tooling, dependency updates         |
| `docs`     | Documentation only                               |
| `refactor` | Code change with no feature or fix               |
| `test`     | Adding or updating tests                         |
| `ci`       | CI/CD pipeline changes                           |
| `perf`     | Performance improvement                          |
| `revert`   | Revert a previous commit                         |

Example: `feat: add DROP TABLE command`

Breaking changes must append `!` after the type, e.g. `feat!: rename CLI entrypoint`.

These prefixes are also used by the **release-drafter** workflow: every PR merged to `main` updates a running GitHub release draft, categorised by type (`feat` → Features, `fix` → Fixes, `docs` → Documentation, `ci`/`chore` → CI / Tooling). When a version tag is pushed, the draft becomes the published release.

## Running Checks Locally

Run all gates with [`just`](https://github.com/casey/just#installation) before pushing:

```bash
just check
```

Or run individual checks:

### Lint

```bash
just lint
# Auto-fix lint and format issues:
just fix
```

### Type checking

```bash
just type
```

### Unit tests

```bash
just test
# With coverage report:
just cov
# With HTML coverage report:
just cov-html
```

**Unit test network policy:** unit tests must not make real network calls.
`tests/unit/conftest.py` installs an autouse fixture (`_block_real_sockets`) that uses
[`pytest-socket`](https://github.com/miketheman/pytest-socket) to replace
`socket.socket` with a stub that raises `SocketBlockedError` immediately.
Any unit test that accidentally reaches a real HTTP client will fail loudly.

- Mock HTTP calls with [`respx`](https://lundberg.github.io/respx/) (preferred) or
  patch `build_http_client` / `open_connection` at the service boundary.
- Unix-domain sockets (AF_UNIX) are permitted because asyncio's event loop requires
  them internally; they cannot reach the internet.
- Integration tests (`tests/integration/`) are exempt — they legitimately call
  real Fabric APIs and are not subject to this fixture.

### Integration tests

Requires a valid `az login` session and a reachable Fabric workspace.

```bash
just integration
```

**Integration test policy:** the integration suite is **label-gated** — it only runs in CI when the `integration` label is applied to a PR. This is intentional: the integration tests hit real Fabric APIs and consume capacity. Maintainers apply the label when a PR touches API-touching behaviour. The integration suite is _not_ a required status check; it is a maintainer-controlled gate.

### Security audit

```bash
just audit
```

### Build wheel/sdist

```bash
just build
```

## Code of Conduct

By participating in this project you agree to abide by the [Code of Conduct](CODE_OF_CONDUCT.md).
