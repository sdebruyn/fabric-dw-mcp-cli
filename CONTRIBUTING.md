# Contributing

Thank you for your interest in contributing to `fabric-dw-mcp-cli`!

## Dev Setup

> **Recommended:** open the repo in [GitHub Codespaces](https://codespaces.new/sdebruyn/fabric-dw-mcp-cli) or VS Code with the Remote-Containers extension. The devcontainer handles all of steps 1–2 automatically.

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
- PR titles must follow [Conventional Commits](#conventional-commits); they become the squash-merge commit message.
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
- Integration tests (`tests/integration/`) are exempt; they legitimately call
  real Fabric APIs and are not subject to this fixture.

### Integration tests

Requires a valid `az login` session and a reachable Fabric workspace.

```bash
just integration
```

**Integration test policy:** the integration suite is **label-gated**: it only runs in CI when the `integration` label is applied to a PR. This is intentional: the integration tests hit real Fabric APIs and consume capacity. Maintainers apply the label when a PR touches API-touching behaviour. The integration suite is _not_ a required status check; it is a maintainer-controlled gate.

### Security audit

```bash
just audit
```

### Build wheel/sdist

```bash
just build
```

## Releasing

The plugin version (`plugin.json`) is auto-bumped from stable git tags by the Publish CI workflow. No release-prep PR is required.

### Stable release (normal flow)

1. **Tag**: run `just tag X.Y.Z` (e.g. `just tag 2026.7.0`) **from a clean, up-to-date local `main`**. The version must follow `YYYY.M.N` (year `20xx`, month `1–12` with no leading zero, patch `0` or a positive integer without a leading zero, no prerelease suffix).
2. **Publish**: the `Publish` CI workflow fires on the tag push, ships the package to PyPI, and creates a GitHub Release.
3. **Auto-bump**: the `sync-plugin-manifest` job (in the same workflow) checks out the default branch, updates `plugins/fabric-dw/.claude-plugin/plugin.json` and its GitHub Copilot CLI mirror `plugins/fabric-dw/.github/plugin/plugin.json` to match the tag, and commits directly using a PAT. No manual PR needed.

### Optional: pre-bump via release-prep PR

If you prefer to commit the `plugin.json` bump before tagging (e.g. for review purposes), `just release X.Y.Z` is still available. It writes the version into both `plugin.json` copies so you can open a release-prep PR. After the PR is merged, `just tag X.Y.Z` proceeds as normal. The CI `sync-plugin-manifest` job will detect that `plugin.json` is already correct and skip the auto-commit.

### Required one-time repo setup

The `sync-plugin-manifest` job pushes a commit directly to the default branch. It uses a fine-grained PAT (stored as a repo secret) to bypass branch protection:

1. **Create the PAT**: an admin account must create a fine-grained Personal Access Token scoped to this repository with **Contents: Read and Write** permission. This allows the push to bypass branch protection rules (because `enforce_admins` is not set on this repo's main branch).
2. **Store it as a secret**: add the PAT as a repository secret named **`RELEASE_BUMP_TOKEN`** under **Settings > Secrets and variables > Actions**.
3. **Protect tags** (strongly recommended): because a `v*` tag push now causes an automated commit straight to the default branch via a PAT, it is important that only authorised maintainers can create `v*` tags. Add a tag protection rule under **Settings > Tags** matching the pattern `v*` so that only repository admins or designated roles can push release tags.

Without `RELEASE_BUMP_TOKEN`, the auto-commit step will fail and `plugin.json` will remain at its previous value on the default branch (the PyPI publish and GitHub Release are unaffected).

### Prerelease builds

Prerelease builds (`.devN` suffix derived by hatch-vcs from commits since the last tag) are published automatically on every push to `main`; `plugin.json` always reflects the latest stable release only. `just release` and `just tag` both reject any prerelease suffix (`aN`/`bN`/`rcN`/`.devN`).

## Code of Conduct

By participating in this project you agree to abide by the [Code of Conduct](CODE_OF_CONDUCT.md).
