# Bowerstone platform

This repository holds the shared base that every Bowerstone product is built on:

- **The engine:** the API server, in Python with FastAPI, on PostgreSQL 18. Each product runs inside it.
- **The screen kit** (`@bowerstone/ui`): the components and styles every product's screens use.
- **The shell** (`@bowerstone/shell`): the one React app that loads each installed product's screens.
- **The desktop wrapper** (`@bowerstone/desktop`): an Electron app with Python and Postgres inside, so a customer installs nothing else.

Products, such as Accounting, live in their own repositories and build on these packages.

The project is in early development. Nothing has been released yet.

## What your computer needs

- [Git](https://git-scm.com/)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- Make. On Windows, install it with `winget install ezwinports.make`. macOS and Linux usually have it already.

You don't need Python, uv, Node, or Postgres on your computer. The engine, the screens, and the database run in the containers that `compose.dev.yaml` describes, and every `make` command runs inside them. The first run downloads the images and installs the packages into Docker volumes, so it takes a few minutes.

## Commands

| Command | Does |
| --- | --- |
| `make` | Lists the commands |
| `make db` | Starts the development database, with the owner and app roles |
| `make psql` | Opens `psql` on the development database as the admin role |
| `make stop` | Stops the development containers and keeps their data |
| `make clean` | Stops the containers and deletes their volumes: the database, packages, and caches |
| `make lint` | Lints and type-checks the engine (ruff, mypy) and the screens (TypeScript) |
| `make test` | Runs the engine's tests (pytest) and the screens' tests (Vitest), starting the test databases first |
| `make format` | Formats the engine's Python code |
| `make migrate` | Brings the development database up to date: the core's migrations, then each enabled product's |

`make dev` and `make desktop` are not set up yet.

The development database listens on `127.0.0.1:15432`, so it does not clash with another Postgres on the usual port. To use a different port, set `BOWERSTONE_DB_PORT` before running `make db`. Its passwords are for local development only, and `compose.dev.yaml` lists them.

The engine's tests never touch the development database. They run on two Postgres servers of their own, one on version 18 and one on version 17 to check that older versions are refused. Both keep their data in memory, aren't reachable from outside Docker, and start with `make test`. Each test gets a fresh database, which is dropped afterwards. `make stop` stops the servers.

## The bowerstone command

The engine is run and managed through one command, `bowerstone`, which also runs as `python -m bowerstone_engine`. The desktop app, Docker, and the hosted version all use it. Its settings come from `BOWERSTONE_` environment variables. `compose.dev.yaml` sets them in the engine container for the development database, so you can run a command there with `docker compose -f compose.dev.yaml run --rm engine uv run bowerstone products list`.

| Command | Does |
| --- | --- |
| `bowerstone setup-database` | Creates the database and its two roles, once, on a new install. It needs an admin's address, from `--admin-url` or `BOWERSTONE_ADMIN_DATABASE_URL` |
| `bowerstone migrate` | Checks the database, then applies the core's and each enabled product's new migrations, as the owner role |
| `bowerstone products list` | Lists every product present: installed, removed, or available |
| `bowerstone products install <key>` | Installs a product and the products it needs |
| `bowerstone products uninstall <key> [--delete-data]` | Removes a product, keeping its data unless told otherwise |
| `bowerstone serve [--host] [--port]` | Checks the database as the app role, then serves the API and the screens. It listens on 127.0.0.1:8000 unless told otherwise |
| `bowerstone check-imports <package>` | Checks that a product uses the engine and other products only through their public modules |

Installing or removing a product takes effect when the engine next starts. Every error prints one sentence and exits with status 1.

## Licence

Bowerstone is licensed under the GNU Affero General Public License, version 3. See [LICENSE](LICENSE).

To report a security problem, see [SECURITY.md](SECURITY.md).
