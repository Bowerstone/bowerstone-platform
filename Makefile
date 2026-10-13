# Commands for bowerstone-platform (Platform Build Plan, task 0.2).
#
# Every target runs inside the development containers in compose.dev.yaml, so a
# command gives the same result on any computer and in the automated builds.
# Each recipe line is one docker compose command, which runs the same way under
# Windows' cmd.exe and a Unix shell. Paths inside the containers are relative to
# /workspace, because Git Bash on Windows rewrites arguments that start with "/".

COMPOSE := docker compose --file compose.dev.yaml
ENGINE := $(COMPOSE) run --rm engine
WEB := $(COMPOSE) run --rm web
GITLEAKS := $(COMPOSE) run --rm gitleaks

.PHONY: help db lint test security migrate dev

help:
	@echo make db: start the development database and wait until it is ready
	@echo make lint: ruff, mypy, the TypeScript check, and the 300-line check
	@echo make test: run the tests
	@echo make security: bandit, pip-audit, npm audit, and gitleaks
	@echo make migrate: not yet set up, task 1.6
	@echo make dev: not yet set up, task 2.2

db:
	$(COMPOSE) up --detach --wait db

lint:
	$(ENGINE) uv run --locked ruff check
	$(ENGINE) uv run --locked ruff format --check
	$(ENGINE) uv run --locked mypy
	$(ENGINE) uv run --locked python scripts/check_file_length.py .
	$(WEB) npm ci --no-audit --no-fund
	$(WEB) npm run typecheck

test:
	$(ENGINE) uv run --locked pytest

security:
	$(ENGINE) uv run --locked bandit --recursive --quiet engine/src scripts
	$(ENGINE) sh -c "uv export --quiet --locked --no-emit-project --format requirements-txt --output-file /tmp/requirements.txt && uv run --locked pip-audit --disable-pip --requirement /tmp/requirements.txt"
	$(WEB) npm audit --audit-level=high
	$(GITLEAKS) git --no-banner --no-color --redact --verbose .
	$(GITLEAKS) git --no-banner --no-color --redact --verbose --pre-commit --staged .

migrate:
	$(error Not yet set up: task 1.6 of the Platform Build Plan builds make migrate)

dev:
	$(error Not yet set up: task 2.2 of the Platform Build Plan builds make dev)
