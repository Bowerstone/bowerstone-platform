# Features

The change log for `bowerstone-platform`, newest entry first. Only Ilia sets an entry to "Approved".

## 2026-10-12: Platform tooling (Platform Build Plan, task 0.2)
- Status: In progress
- What and why: Sets up the development tooling so that a computer with only Git, Make, and Docker Desktop can work on the platform. Postgres 18, Python 3.13 with `uv`, and Node 22 run in containers defined in `compose.dev.yaml`, and every `make` target runs through them.
  - `make db` starts Postgres 18 with the built-in `C.UTF-8` locale and the `bowerstone_owner` and `bowerstone_app` roles, and waits for a health check that confirms all three.
  - `make lint` runs ruff, mypy in strict mode, the TypeScript check, and the 300-line file check.
  - `make test` runs the tests, including the guard hook's tests from `.claude/hooks/`.
  - `make security` runs bandit, pip-audit, npm audit, and gitleaks. CLAUDE.md rule 7 runs them on every change, so they start here rather than in task 6.3.
  - `make migrate` and `make dev` stop with "Not yet set up" until tasks 1.6 and 2.2.
- Files: in progress
- Tests: in progress
- Scaling and security: in progress
- Dependencies added: in progress
