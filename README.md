# bowerstone-platform

Bowerstone is a family of open-source apps for small businesses (Accounting first, then Invoicing, then a CRM) that all run on one shared engine. This repository holds the shared base:

- **The engine** (`bowerstone-engine` on PyPI): the one backend program, in Python with FastAPI, that runs every installed app.
- **The design system** (`@bowerstone/ui` on npm): the shared React components that every app's screens use.
- **The shell** (`@bowerstone/shell` on npm): the one React app that shows the screens of every installed app.
- **The Docker image** that runs them with PostgreSQL 18.

Each app has its own repository. The plans and decisions behind this one are in `docs/decisions/`.

This repository is at an early stage. It holds the development tooling, and the engine, design system, and shell come next.

## Requirements

- Git
- Make. On Windows, install it with `winget install ezwinports.make`.
- Docker Desktop

Nothing else is installed for the project's code. Python, Node, and PostgreSQL run in the development containers defined in `compose.dev.yaml`.

## Getting started

1. Copy `.env.example` to `.env`, and give each line a made-up password that you use nowhere else. `.env` is never committed.
2. Run `make db`, `make lint`, and `make test`.

The first run downloads the container images and packages, so it takes a few minutes.

## Commands

| Command | What it does |
| --- | --- |
| `make db` | Starts the development database (PostgreSQL 18 with both Bowerstone roles) and waits until it is ready |
| `make lint` | Runs ruff, mypy, the TypeScript check, and the 300-line file check |
| `make test` | Runs the tests |
| `make security` | Runs bandit, pip-audit, npm audit, and gitleaks |
| `make migrate` | Not yet set up |
| `make dev` | Not yet set up |

## Automated checks

GitHub Actions runs `make lint`, `make test`, and `make security` on every push and every pull request, in the same containers as above. The workflow is `.github/workflows/checks.yml`. Each run makes its own throwaway database passwords, so it needs no GitHub secrets.

## Working with Claude Code

Claude Code works in this repository under the rules in `CLAUDE.md`. A guard hook in `.claude/` stops it from committing, pushing, or reading `.env`.

The hook needs Python 3 on the computer, available as `python3`. On Windows, check that `python3 --version` works in Git Bash. Contributors who do not use Claude Code do not need Python.

## Reporting a security problem

See `SECURITY.md`. Please do not open a public issue.

## Licence

GNU Affero General Public License, version 3 only (`AGPL-3.0-only`). See `LICENSE`.
