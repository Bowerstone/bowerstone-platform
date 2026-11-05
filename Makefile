# Every target runs inside the development containers in compose.dev.yaml, so a
# command gives the same result on any computer and in GitHub Actions.
#
# The recipes are plain commands with no shell syntax, because make runs them
# with cmd.exe on Windows and with sh everywhere else. Help text goes through
# $(info), and `@cd .` is a silent step that works in both shells.

COMPOSE := docker compose --file compose.dev.yaml
RUN := $(COMPOSE) --progress quiet run --rm
UV := $(RUN) engine uv run --locked
NPM := $(RUN) web npm

.DEFAULT_GOAL := help
.PHONY: help db psql stop clean lint lint-engine lint-web test test-engine test-examples test-web test-databases format engine-image migrate dev desktop

help:
	$(info Bowerstone platform. Every target runs in Docker.)
	$(info )
	$(info make db       Start the development database, with the owner and app roles)
	$(info make psql     Open psql on the development database as the admin role)
	$(info make stop     Stop the development containers and keep their data)
	$(info make clean    Stop the containers and delete their volumes: the database, packages, and caches)
	$(info make lint     Lint and type-check the engine and the screens)
	$(info make test     Run the engine's, the example products', and the screens' tests, on throwaway test databases)
	$(info make format   Format the engine's Python code)
	$(info make migrate  Bring the development database up to date: the core and each enabled product)
	$(info make dev      Not yet set up)
	$(info make desktop  Not yet set up)
	@cd .

db:
	$(COMPOSE) up --detach --wait db

psql: db
	$(COMPOSE) exec db psql --username=bowerstone_admin --dbname=bowerstone

stop:
	$(COMPOSE) down

clean:
	$(COMPOSE) down --volumes

lint: lint-engine lint-web

lint-engine: engine-image
	$(UV) ruff check
	$(UV) ruff format --check
	$(UV) mypy

lint-web:
	$(NPM) run lint

test: test-engine test-examples test-web

test-engine: engine-image test-databases
	$(UV) pytest

# Each example product runs its own tests with the testing kit, as a product repository does.
test-examples: engine-image test-databases
	$(UV) pytest tests/example_products/notes
	$(UV) pytest tests/example_products/tasks

test-web:
	$(NPM) test

# The tests' own Postgres 18 and 17 servers, held in memory. `make stop` stops them.
test-databases:
	$(COMPOSE) --progress quiet up --detach --wait test-db test-db-17

# Write mode, so it is limited to the engine's folder.
format: engine-image
	$(UV) ruff check --fix engine
	$(UV) ruff format engine

# Rebuilds the engine image when its Dockerfile has changed. Docker's cache makes this quick.
engine-image:
	$(COMPOSE) --progress quiet build engine

migrate: engine-image db
	$(UV) bowerstone migrate

dev desktop:
	$(info Not yet set up)
	@cd .
