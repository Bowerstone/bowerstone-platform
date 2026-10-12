# Bowerstone: Platform Build Plan

Oct 12, 2026 · Prepared by Ilia Fatemi · Status: plan for Claude Code to follow, no code written yet

This is the step-by-step plan Claude Code follows to build the base of the Bowerstone platform. It turns the Platform Architecture note into tasks across separate repositories. Each task names its repository, its files, its design details, and a "done when" check. The ledger schema and its safeguards come from the Initial Setup Plan, which stays the reference for that part.

## How Claude Code uses this plan

- **The rules come first.** The CLAUDE.md of the repository being worked on applies to every task. Where this plan and CLAUDE.md disagree, stop and ask Ilia.
- **One task per review.** Each task below is one change in one repository: plan it, build it, log it in that repository's `features.md`, stage it, write the review summary, and stop.
- **Plan before code.** Before each task, Claude Code writes a short plan for that task, naming the files, the tests, and the risks, then waits for Ilia's approval.
- **Order matters.** Tasks run in the order given. A task may start only when the tasks it depends on are approved and, where it needs a published package, released.
- **Design details are a starting point.** Where a detail turns out to be wrong in practice, Claude Code says so in the task plan and proposes the change. It never silently departs from this plan.

## What the base must prove

When phases 0 to 6 are done:

- [ ] The engine, the design system, and the shell are published from `bowerstone-platform`, and Accounting and Invoicing are published from their own repositories
- [ ] `pip install bowerstone-invoicing` brings in the engine and Accounting, and `bowerstone apps install invoicing` installs both apps in dependency order
- [ ] `bowerstone apps list`, `install`, and `uninstall` work, and the dependency rules hold
- [ ] The engine refuses to start in production mode, because sign-in is not built yet
- [ ] Every ledger rule from L1 to L11 has a passing test against real Postgres 18
- [ ] Issuing an invoice posts its accounting entry in the same transaction. If the books refuse the entry, the invoice stays a draft
- [ ] One React shell loads the screens of each installed app at run time, and every screen uses the shared design system
- [ ] `docker compose up` in `bowerstone-platform` starts Postgres 18 and the engine with the released apps
- [ ] An end-to-end test creates accounts, posts an entry, issues an invoice, and sees all of it on the trial balance
- [ ] Every repository runs its checks on every change

## Not part of this plan

These come after the base and get their own plans:

- Sign-in, users, and roles screens
- Backups, restore, export, and the backup file used to move to the web version
- The CRM app
- Sales tax
- The launcher (desktop phase)
- The control plane and payments (private repository)
- Hosted operations

## Decisions this plan depends on

| Decision | Needed before | Status |
| --- | --- | --- |
| One company per installation (Charter, decision 8) | Task 1.3 | Proposed. Confirm with the partner |
| Correction by reversal only (Charter, decision 7) | Task 4.2 | Proposed. Confirm with the partner |
| Rounding: half-up to the cent, remainder on the last line | Task 1.5 | Proposed. Confirm with the partner |
| How packages are signed for the launcher | Task 3.1 | To decide in that task. Ilia approves |
| PyPI and npm accounts, the `@bowerstone` npm organization, and trusted publishing set up for each package | Task 3.1 | Ilia does this by hand |
| The dependencies listed at the end of this plan | Task 0.2 and each task that adds one | Ilia approves each one (rule 6) |

## Repositories in this plan

| Repository | Created in | Publishes |
| --- | --- | --- |
| `bowerstone-platform` | Task 0.1 | `bowerstone-engine` (PyPI), `@bowerstone/ui` and `@bowerstone/shell` (npm), the Docker image |
| `bowerstone-app-template` | Task 3.2 | Nothing |
| `bowerstone-accounting` | Task 4.1 | `bowerstone-accounting` (PyPI, screens inside), signed GitHub release |
| `bowerstone-invoicing` | Task 5.1 | `bowerstone-invoicing` (PyPI, screens inside), signed GitHub release |

All of them live under the GitHub organization `bowerstone`.

### `bowerstone-platform` layout

```text
bowerstone-platform/
├── CLAUDE.md, features.md, README.md, LICENSE, SECURITY.md
├── pyproject.toml            # uv project: the engine
├── package.json              # npm workspaces: web/ui and web/shell
├── Makefile                  # the commands in CLAUDE.md
├── compose.yaml, compose.dev.yaml, Dockerfile, distribution.toml
├── docker/initdb/01-roles.sh
├── .claude/                  # settings and the guard hook
├── .github/workflows/
├── engine/src/bowerstone_engine/
│   ├── config.py, db.py, errors.py, types.py, deps.py
│   ├── migrate.py            # migration runner and database check
│   ├── apps.py               # app manifest and loader
│   ├── installer.py          # install, update, remove apps
│   ├── auth.py               # permissions on every route
│   ├── http.py               # security headers, body limit, error shape
│   ├── screens.py            # serves each installed app's screens
│   ├── importcheck.py        # the boundary check apps run in their builds
│   ├── testing.py            # test fixtures for app repositories
│   ├── main.py, cli.py
│   └── core/                 # shared data: company, users, customer list, history, events
│       ├── migrations/0001_core.sql (+ .down.sql)
│       ├── public.py         # what apps may call
│       └── routes.py
├── engine/tests/
├── web/ui/                   # @bowerstone/ui, the design system
├── web/shell/                # @bowerstone/shell, the one React app
├── tests/example_app/        # a small test-only app that proves loading and screens
├── docs/decisions/           # copies of the decision notes
└── scripts/check_file_length.py  # the 300-line check
```

### App repository layout

```text
bowerstone-accounting/
├── CLAUDE.md, features.md, README.md, LICENSE
├── pyproject.toml            # package bowerstone-accounting, needs bowerstone-engine in a range
├── package.json              # needs @bowerstone/ui and @bowerstone/shell in a range
├── compose.dev.yaml, Makefile
├── .claude/, .github/workflows/
├── src/bowerstone_accounting/
│   ├── manifest.py           # the app's manifest
│   ├── public.py             # what other apps may call, such as post_entry
│   ├── service.py, routes.py, schemas.py
│   ├── migrations/0001_accounting.sql (+ .down.sql)
│   └── screens/              # the built screens, generated at release, never edited
├── web/src/                  # the screens' source
├── tests/
└── docs/decisions/
```

## Phase 0: The repositories

### Task 0.1: Create the organization and the platform repository (Ilia)

Ilia does this task by hand:

- Create the GitHub organization `bowerstone` and the repository `bowerstone-platform`.
- Add `.gitattributes` with the line `* text=auto eol=lf`, before any script or migration is committed. Windows Git otherwise checks files out with Windows line endings, which break shell scripts in the containers and change migration checksums. If files are already committed, run `git add --renormalize .` after adding it.
- Copy in the `dot-claude` folder from the Repository Starter, renamed to `.claude`.
- Use the new CLAUDE.md. It replaces the starter's CLAUDE.md and its `docs/` rule files.
- Add the AGPL-3.0 licence file from the official text.
- Install Python 3 on the computer, only to run the guard hook. Check that `python3 --version` works in Git Bash, because Claude Code runs the hook with `python3`. If the hook command cannot start, Claude Code does not block the command, so a missing `python3` turns the guard off without warning.
- Run the hook tests.
- Check the hook live: ask Claude Code to run `git commit --dry-run`, and confirm the hook blocks it. The dry run creates no commit even if the hook fails.
- Copy the decision notes into `docs/decisions/` by hand: the Platform Architecture note and this note (as `platform-build-plan.md`).
- Make the first commit, and protect `main`.

**Done when:** the repository is on GitHub with these files, `main` is protected, and the live check shows the hook blocking `git commit --dry-run`.

### Task 0.2: Platform tooling

Repository: `bowerstone-platform`.

- **Tools on the development computer:** Git, Make, and Docker Desktop only. On Windows, Make is installed with `winget install ezwinports.make`. Python, `uv`, Node, and Postgres are never installed on the computer for the project's code.
- **Everything runs in Docker.** `compose.dev.yaml` runs the code in containers, with pinned image versions:
  - `db`: Postgres 18 with the built-in locale. `docker/initdb/01-roles.sh` creates the two roles, as in the Initial Setup Plan, section "Database roles". The tests from task 1.1 onwards use it.
  - `engine`: Python 3.13 with `uv`. The repository is mounted into it, and its packages live in a named volume. Lint, type checks, tests, migrations, and the development server run here.
  - `web`: Node 22. The repository is mounted into it, and `node_modules` lives in a named volume. The screens' build, tests, and development server run here.
  - Playwright runs in the official Playwright image, from task 2.2.
- **Python:** one `uv` project for the engine. Development tools go in a `dev` dependency group: `pytest`, `httpx`, `hypothesis`, `ruff`, and `mypy`.
- **npm:** workspaces for `web/ui` and `web/shell`.
- **Makefile:** every target runs inside the containers through `docker compose`, so a command gives the same result on any computer and in the automated builds. `lint`, `test`, and `db` work. The other targets print "Not yet set up" until the task that builds them: `migrate` in task 1.6, `dev` in task 2.2.
- **Lint settings:**
  - `ruff` uses rules `E F W I B UP S SIM RUF`.
  - `mypy` runs in strict mode.
  - TypeScript runs with `strict: true`.
  - `scripts/check_file_length.py` fails on any source file over 300 lines (CLAUDE.md rule 8).
- **CLAUDE.md:** the Context section names this repository and its paths.
- **README.md:** what the repository is, and how to start.
  - **Requirements:** Git, Make, and Docker Desktop. Nothing else is installed for the project's code.
  - **Working with Claude Code:** Python 3, only for the guard hook in `.claude/`. A separate heading, so contributors who do not use Claude Code know they do not need it.
  - **Commands:** `make db`, `make lint`, and `make test`, with more added by later tasks.
- **SECURITY.md:** how to report a security problem.
  - Reports go through GitHub's private vulnerability reporting, never public issues. Ilia turns it on in the repository settings.
  - Before version 1.0, only the latest release gets security fixes.
  - It states how quickly a report is answered. Ilia sets the time before the file is committed.

**Done when:** on a computer with only Git, Make, and Docker Desktop, `make db` starts the development database with both roles, and `make lint` and `make test` run in the containers on the empty project and pass.

## Phase 1: The engine

All tasks in this phase are in `bowerstone-platform`.

### Task 1.1: Settings, connections, and the database check

- **`config.py`:** settings read from `BOWERSTONE_` environment variables:
  - `env` (`development` or `production`)
  - `database_url` (app role) and `owner_database_url` (owner role)
  - `static_dir`
  - `max_body_bytes`, `statement_timeout_ms`, and the pool sizes
- **`db.py`:**
  - The pool has a fixed size.
  - Every connection sets `statement_timeout`.
  - `set_current_user(conn, user_id)` runs `set_config('bowerstone.user_id', ..., true)` for the change history.
- **The database check** refuses to continue unless all of these hold:
  - the server is PostgreSQL 18
  - the database uses the built-in `C.UTF-8` locale
  - `bowerstone_owner` and `bowerstone_app` exist
  - the current user is `bowerstone_owner`

The engine's own test fixtures start here. They create a fresh test database through an admin connection read from `BOWERSTONE_TEST_ADMIN_URL`, a setting used only by tests. Task 1.8 turns these fixtures into the testing kit.

**Done when:** tests show that the check refuses a database with the wrong locale, missing roles, or the wrong user. They also show that the statement timeout stops a slow query.

### Task 1.2: The migration runner

The runner is a small engine module. It replaces Alembic, which the Initial Setup Plan named. Alembic would need one migration branch per app, plus version folders added at run time, which is harder to follow than plain numbered files.

- **Files.** Each module (the core and every app) keeps numbered files in its own `migrations` package:
  - `0001_name.sql` is applied on upgrade.
  - `0001_name.down.sql` is applied on downgrade, or when an app's data is deleted.
- **Record and checksum.**
  - Applied files are recorded in `core.applied_migration`, along with module, version, name, checksum, and time.
  - The runner creates the `core` schema and this table itself if they are missing, before the first core migration runs, so `0001_core.sql` does not create them.
  - The runner refuses to continue if a file changed after it was applied. This enforces the rule that released migrations are never edited.
  - The checksum is taken over the file's exact bytes. The runner refuses any migration file that contains a Windows line ending, and names the file, so a wrong checkout fails loudly instead of looking like an edited migration.
- **Lock.** A session-level Postgres advisory lock, held for the whole run, stops two runs from changing the schema at once. A lock that lasts one transaction is not enough, because each file runs in its own transaction.
- **Transactions.** Each upgrade runs in one transaction with its record.
- **Files outside a transaction.** A migration file whose first line is `-- bowerstone: no-transaction` runs outside a transaction, and may hold only one statement. Its record is written after the statement succeeds. This allows `CREATE INDEX CONCURRENTLY` on large tables.
- **How files run.** The runner executes each file through the raw driver cursor with no parameters. This lets a file hold many statements, and it leaves `%` signs alone. The ledger's error messages use `%`, and passing them as parameters would break them.
- **Numbering.** Numbers must run 0001, 0002, and so on, with no gaps.

**Done when:** tests cover:

- a fresh upgrade
- a second run that changes nothing
- an edited file, which is refused
- a gap in numbering, which is refused
- a downgrade
- two runs at the same time
- a no-transaction file that runs `CREATE INDEX CONCURRENTLY`
- a no-transaction file with two statements, which is refused

### Task 1.3: The core schema

`core/migrations/0001_core.sql` creates the data every app shares. It runs as `bowerstone_owner`.

| Table | Holds |
| --- | --- |
| `core.company` | One row: name, currency, and the company's time zone |
| `core.role`, `core.permission`, `core.role_permission` | Roles and permissions as data. Each app adds its own permissions when installed |
| `core.app_user` | People who can sign in (empty until sign-in is built) |
| `core.party` | The shared customer and supplier list |
| `core.installed_app` | Each installed app: key, version, the apps it needs, enabled or not |
| `core.audit_log` | The change history. Append-only, even for the owner role |
| `core.event` | Events written in the same transaction as the change |

Two functions come with it:

- **`core.write_audit()`** is the audit trigger function every app attaches to its tables. It is `SECURITY DEFINER`, uses `search_path = pg_catalog, pg_temp`, and leaves out `password_hash`. It records the table as `schema.table`, so `accounting.settings` and `invoicing.settings` stay apart.
- **`core.emit_event(type, source, record_type, record_id, payload)`** is how an app announces a change.

Starting data:

- one company row
- the roles `owner`, `staff`, and `read_only`
- the core permissions `core.view`, `core.parties`, `core.company`, and `core.users`, with their default roles

What `bowerstone_app` gets:

- `SELECT` on the core tables
- column-level `UPDATE` where needed
- `EXECUTE` on `emit_event`
- no write access to `audit_log`, `installed_app`, or the permission tables

**Done when:** tests as `bowerstone_app` show it cannot write the audit log, empty a table, switch off a trigger, or change installed apps. Changes to parties appear in the audit log.

### Task 1.4: The app manifest and the installer

Each app declares a manifest under the Python entry point group `bowerstone.apps`:

```python
APP = AppManifest(
    key="invoicing",                 # also its schema name and URL prefix
    name="Invoicing",
    version="0.1.0",
    migrations_package="bowerstone_invoicing.migrations",
    router=build_router,             # its FastAPI routes
    screens_package="bowerstone_invoicing.screens",
    requires=("accounting",),
    engine_versions=">=0.1,<0.2",    # the engine versions it works with
    ui_versions=">=0.1,<0.2",       # the design system versions its screens work with
    permissions=(Permission("invoicing.issue", "Issue invoices", ("owner", "staff")), ...),
    data_deletable=True,             # False for Accounting
)
```

Every manifest must meet these rules:

- **Key.** Lower case, 2 to 31 characters, and not reserved (`core`, `public`, `api`, and so on).
- **Permission names.** Every permission starts with the app's key.
- **Schema.** The app's first migration creates a schema with the same name as its key.
- **Engine version.** This engine's version is inside the app's range.

`installer.py` provides three functions:

| Function | Behaviour |
| --- | --- |
| `upgrade_all` | The database check, then core migrations, then each enabled app's new migrations, in dependency order. It stops if an enabled app's package is missing |
| `install(key)` | Installs the app and everything it needs, applies their migrations, and registers their permissions. Installing an app that was removed restores it with its data |
| `uninstall(key, delete_data=False)` | Refused while an enabled app needs it. Without `delete_data`, it marks the app disabled and keeps its data. With `delete_data`, it runs the down migrations, which is refused for apps whose data may not be deleted, such as Accounting |

**Done when:** tests use two small test-only apps to cover:

- the install order
- a missing dependency
- a loop of dependencies
- removing an app that another app needs
- removing and then restoring an app
- deleting data
- refusing to delete data that may not be deleted
- an engine version outside the range
- a bad app key

### Task 1.5: The API host

- **`main.py`:** `create_app(settings)` builds the API.
  - It refuses to start when `env` is `production`, because sign-in is not built.
  - It mounts the routes of each enabled app under `/api/v1/<key>`, and the core routes under `/api/v1/core`.
  - It serves the built shell from `static_dir`, with unknown paths falling back to the shell. Unknown `/api/` paths still return the JSON error shape.
- **`auth.py`:** every route uses `require("<permission>")` or `public()`.
  - In development, requests run as a built-in local user with the Owner role's permissions. This user has no row in `core.app_user`, so its user ID is empty, and `created_by`, `posted_by`, and the change history record no user until sign-in exists.
  - A test walks every route of the built app and fails on any route that uses neither.
- **`http.py`:**
  - The standard error shape is `{"rule": "...", "message": "..."}`. It uses status 422 for broken rules, 404 for missing records, and 500 for unexpected errors. A 500 never includes details.
  - Input errors use the rule `input` and list each field.
  - Security headers and a request ID go on every response, including errors.
  - A request body limit is enforced while the body streams in, not only from `Content-Length`.
- **`types.py`:**
  - `Money` is a `Decimal` with at most 19 digits and 4 decimal places. It is sent as a string, and floats are refused.
  - `round_money` rounds half-up to cents.
  - `Page` is a list page with `next_after` for keyset pagination, up to a maximum of 200 per page.
- **Core routes:**
  - `GET /api/v1/health` is public.
  - `GET /api/v1/apps` lists the installed apps, with their versions and screen file addresses.
  - `GET` and `PATCH` on `/api/v1/core/company`.
  - `GET` and `POST` on `/api/v1/core/parties`, and `GET` on `/api/v1/core/parties/{id}`.

**Done when:** tests cover:

- refusal in production mode
- the permission check on every route
- the headers on success and error responses
- a refused oversized body
- a refused float amount
- the error shape for each status
- pagination across two pages
- the shell fallback

### Task 1.6: The command-line tool

`bowerstone` is the contract the launcher will use later.

| Command | Does |
| --- | --- |
| `bowerstone migrate` | `upgrade_all` as the owner role. Prints each migration applied |
| `bowerstone apps list` | Every app present, and whether it is installed, removed, or available |
| `bowerstone apps install <key>` | Installs it and what it needs |
| `bowerstone apps uninstall <key> [--delete-data]` | Removes it, keeping its data unless told otherwise |
| `bowerstone serve` | Starts the API as the app role |
| `bowerstone check-imports <package>` | Runs the boundary check (task 1.7) |

Installing or removing an app takes effect when the engine next starts, because routes are mounted at start. The commands say so.

Errors print one plain sentence and exit with status 1. They never print a stack trace.

**Done when:** tests run each command against a test database and check the output and the exit status.

### Task 1.7: The import check

`importcheck.py` reads the import statements of an app package and fails in either case:

- The app imports anything from the engine except its public modules:
  - `bowerstone_engine.core.public`
  - `bowerstone_engine.types`
  - `bowerstone_engine.errors`
  - `bowerstone_engine.auth`
  - `bowerstone_engine.apps`
  - `bowerstone_engine.deps`
- The app imports another app other than through that app's `public` module, or imports an app its manifest does not list in `requires`.

Every app repository runs `bowerstone check-imports` in its automated build.

**Done when:** the check passes on a correct test app, and fails on a test fixture that breaks each rule.

### Task 1.8: The testing kit for apps

`bowerstone_engine.testing` is a pytest plugin that every app repository uses:

- It creates a fresh Postgres 18 test database with the two roles and the built-in locale.
- It runs the core migrations, then installs the app under test and the apps it needs.
- It gives each test a connection as `bowerstone_app`, rolled back afterwards, plus a separate committed database for race tests.
- It gives an API client for the app's routes.

**Done when:** `tests/example_app` in this repository uses the kit for its own tests, and they pass.

## Phase 2: The screens

All tasks in this phase are in `bowerstone-platform`.

### Task 2.1: The design system

`@bowerstone/ui` is the one place for how Bowerstone looks:

- **Tokens:** colours for light and dark themes, type, and spacing, all as CSS variables.
- **Components:**
  - `AppFrame`, which holds the navigation and the page
  - `PageHeader`, `Button`, `Field`, `TextInput`, and `Select`
  - `Table`
  - `MoneyText`, right-aligned in tabular figures
  - `Notice`, for errors, and `EmptyState`
- **`money.ts`:** parsing, adding, and formatting with `decimal.js`, never `number`.
- **`api.ts`:** a typed fetch helper that turns the standard error shape into a readable message.

Check every component against the design guides in CLAUDE.md rule 4.

**Done when:** component tests pass, including keyboard use and screen-reader labels. Money tests include `0.1 + 0.2` and very large amounts.

### Task 2.2: The shell and loading app screens

`@bowerstone/shell` is the one React app, built with Vite. It loads each app's screens at run time.

- **The screen contract.** An app's screens are built, in Vite library mode, into one ES module and one CSS file. The module exports one object: the app's key, its name, its menu items, and its pages.
  - React, React DOM, TanStack Query, and `@bowerstone/ui` are left out of the build.
  - The shell ships each of them as a separate ES module and provides them through an import map, so every app uses the shell's copy.
  - The import map is an inline script, so the Content Security Policy lists its hash, computed when the shell is built.
- **Serving.** `screens.py` in the engine serves each installed app's built screens from its package at `/apps/<key>/screens/`. `GET /api/v1/apps` lists their addresses.
- **Loading.**
  - The shell reads `GET /api/v1/apps` and loads each app's module with `import()`.
  - It refuses an app whose `ui_versions` range does not include its own design system version, and shows a clear message instead.
  - A removed app's screens disappear.
- **Security.** Screens load only from the engine's own address, so the Content Security Policy keeps `script-src 'self'`. Nothing loads from outside the installation.
- **Routing.** A small built-in router, `#/<app>/<page>`, avoids another dependency.
- **Development servers.** Saved changes on a Windows drive do not reach the containers as file events, so `compose.dev.yaml` sets the Vite server and the engine's reload to poll for changes: `server.watch.usePolling` in Vite, and `WATCHFILES_FORCE_POLLING=true` for the engine.
- **API types.** TypeScript types are generated from the engine's OpenAPI schema with `openapi-typescript`. The generated file is committed, and the automated build checks that it is current.

**Done when:**

- The example test app's screens load in the shell.
- A screen whose `ui_versions` range excludes the shell's version is refused with the message.
- The navigation follows the installed apps.
- The production build works.

## Phase 3: Publishing and the app template

### Task 3.1: Releases from the platform

Repository: `bowerstone-platform`.

- **When.** A release runs when Ilia pushes a version tag, such as `v0.1.0`. Claude Code never pushes tags.
- **What it publishes:**
  - `bowerstone-engine` to PyPI
  - `@bowerstone/ui` and `@bowerstone/shell` to npm
  - a GitHub release with the built files
- **How.** Publishing uses PyPI and npm trusted publishing from GitHub Actions, so no publishing token is stored anywhere.
- **Versions.** All three share one version number. Until 1.0, a minor version may break things, and the change log in `features.md` says so.
- **Signing.** Packages are signed for the launcher. This task proposes the signing method, and Ilia approves it before the task is built.

**Done when:** a test release to TestPyPI and a test npm tag succeeds from a tag. A package installed from TestPyPI runs `bowerstone --help`.

### Task 3.2: The app template

Repository: `bowerstone-app-template`. Ilia creates it and copies in `.claude/` by hand, because Claude Code never edits that folder. Claude Code fills in the rest.

It holds the app repository layout shown above, with:

- **Contents:**
  - a working empty app: a manifest, one migration, one route, one screen, and their tests
  - `compose.dev.yaml`, the Makefile, and the automated builds, with everything running in Docker as in task 0.2
  - `make dev` and `make e2e`, which start the engine with the app installed and serve the published `@bowerstone/shell`, so the app's Playwright tests can run
- **The automated builds run:**
  - lint and the 300-line file check
  - `mypy` in strict mode
  - `bowerstone check-imports`
  - the tests with the testing kit
  - the screens build and its tests
  - `bandit`, `pip-audit`, `npm audit`, and `gitleaks`
- **Releases.** On a version tag pushed by Ilia, the screens are built into the package, then it is published to PyPI and attached, signed, to a GitHub release.
- **CLAUDE.md.** The same rules, with the Context section left for each app to fill in.
- **README.md.** The same Requirements and Working with Claude Code sections as the platform's, from task 0.2.

**Done when:** a repository created from the template passes its automated builds without changes.

## Phase 4: The Accounting app

### Task 4.1: Create the repository (Ilia)

Ilia creates `bowerstone-accounting` from the template, fills in CLAUDE.md's Context section, copies the decision notes into `docs/decisions/` by hand, and protects `main`. The notes are the Platform Architecture note, this Build Plan (as `platform-build-plan.md`), the v1 ledger architecture note, the Initial Setup Plan, and the v1 Charter.

**Done when:** the repository passes its automated builds.

### Task 4.2: The accounting schema

Repository: `bowerstone-accounting`.

`src/bowerstone_accounting/migrations/0001_accounting.sql` creates the `accounting` schema. It uses the safeguards from the Initial Setup Plan, section "Ledger schema", with these changes:

- **Tables move into the schema.** All tables live in `accounting`, and every name inside functions is qualified with its schema. The `SECURITY DEFINER` functions use `search_path = pg_catalog, pg_temp`.
- **A new settings table.** The lock date and the entry counter move from `company` into `accounting.settings`, which has one row. They belong to the books, not to the company.
- **History and events use the core.** The audit triggers use `core.write_audit()`. The posting event uses `core.emit_event('accounting.entry_posted', ...)`.
- **The line guard keeps its lock.**
  - It locks the entry row with `SELECT ... FOR SHARE`, because the race test showed it is needed (Initial Setup Plan, section "How the schema was checked").
  - It uses one `SELECT ... INTO` per entry ID, not an `EXISTS` subquery, because the subquery form did not lock.
- **The app role stays limited.** `bowerstone_app` gets the same limited grants as before. It cannot change the counter, delete accounts, or write the audit log.
- **Starting data moves out.** The roles and their permissions at the end of the SQL leave this file.
  - The roles live in the core schema (task 1.3).
  - The app's permissions move into its manifest with the `accounting.` prefix, such as `accounting.accounts` instead of `accounts.manage`, and the installer registers them (task 1.4).

The down migration drops the `accounting` schema. The installer still refuses to run it, because Accounting's data may not be deleted.

**Done when:** every test in the Initial Setup Plan, section "Ledger rule tests", passes on the new schema, as `bowerstone_app`, on Postgres 18, using the testing kit. That covers L1 to L11, the safeguards, and the race test in both orders.

### Task 4.3: Accounting's public interface

Repository: `bowerstone-accounting`.

`bowerstone_accounting.public` is the only way other apps use Accounting:

```python
def post_entry(conn, *, entry_date, description, lines, source, source_ref, user_id) -> PostedEntry: ...
def account_by_id(conn, account_id) -> Account | None: ...
```

- **Same transaction.** `post_entry` creates the entry and its lines and posts them, all within the caller's transaction.
- **Errors.** It turns database errors into `LedgerRuleError` with the rule ID, such as `L1` or `L11`.
- **Retries.** Calling it again with the same `source` and `source_ref` returns the entry that already exists. Retries never create a second one.
- **A promise.** These functions are tested as a contract in `tests/contract/`. A change that breaks a caller needs a new major version.

**Done when:** contract tests cover a balanced post, each rule error, a retry that returns the same entry, and a call whose transaction is later rolled back, which leaves no entry.

### Task 4.4: Accounting routes

Repository: `bowerstone-accounting`. The routes sit under `/api/v1/accounting`:

| Route | Permission |
| --- | --- |
| `GET /accounts`, `POST /accounts`, `PATCH /accounts/{id}` | `accounting.view`, `accounting.accounts` |
| `GET /entries`, `POST /entries` (accepts `Idempotency-Key`), `GET /entries/{id}`, `DELETE /entries/{id}` (drafts only) | `accounting.view`, `accounting.draft` |
| `POST /entries/{id}/post`, `POST /entries/{id}/reverse` | `accounting.post` |
| `GET /reports/trial-balance?as_of=` | `accounting.view` |
| `GET /settings`, `PUT /settings` (the lock date) | `accounting.view`, `accounting.lock` |

Lists use keyset pagination. The trial balance is calculated in SQL from posted entries only.

**Done when:** the API tests cover every route for success, each error, and the permission check. A test also shows the same `Idempotency-Key` creates one entry.

### Task 4.5: Accounting screens and first release

Repository: `bowerstone-accounting`.

- **Screens:**
  - Accounts: list and add
  - Entries: list, new entry with running totals, post, and reverse
  - Trial balance: any date
- **Before review.** Each screen goes through `break-ui` worst-case data.
- **Release.** Ilia tags `v0.1.0`, which publishes the package with its screens.

**Done when:**

- Component tests pass.
- A Playwright test in this repository creates accounts, posts an entry, and checks the trial balance.
- `pip install bowerstone-accounting` from the release, followed by `bowerstone apps install accounting`, works on a clean database.

## Phase 5: The Invoicing app

### Task 5.1: Create the repository (Ilia)

Ilia creates `bowerstone-invoicing` from the template, as in task 4.1. Its `pyproject.toml` lists `bowerstone-accounting` within a version range.

### Task 5.2: The invoicing schema

Repository: `bowerstone-invoicing`. `0001_invoicing.sql` creates the `invoicing` schema:

| Table | Holds |
| --- | --- |
| `invoicing.settings` | One row: the receivable account and the revenue account, which reference `accounting.account`, plus the next invoice number |
| `invoicing.invoice` | Customer (`core.party`), issue date, due date, status (draft or issued), invoice number, total, and the accounting entry it created |
| `invoicing.invoice_line` | Description, quantity, unit price, and amount |

The schema enforces these rules:

- `amount = round(quantity * unit_price, 2)`. Postgres rounds half away from zero, which equals half-up for positive amounts.
- An issued invoice and its lines can never be changed or deleted, enforced by a trigger, as for posted entries.
- An invoice number is given only when the invoice is issued, from a counter with no gaps.
- The audit triggers come from the core.

**Done when:** schema tests as `bowerstone_app` cover each constraint and the lock on issued invoices.

### Task 5.3: Issuing an invoice

Repository: `bowerstone-invoicing`. Issuing runs in one transaction:

1. Lock the invoice row. It must be a draft, have at least one line, and have a total above zero.
2. Read the settings. Both accounts must be set.
3. Call `bowerstone_accounting.public.post_entry`:
   - Debit the receivable account with the total, and credit the revenue account with the total.
   - Use `source="invoicing"` and `source_ref=<invoice id>`.
4. Take the next invoice number. Mark the invoice issued and store the entry ID.

If Accounting refuses the entry, for example because of the lock date, the whole transaction rolls back and the invoice stays a draft. Sales tax is not part of this task.

Routes under `/api/v1/invoicing`:

- the invoice list and detail
- create and delete a draft
- `POST /invoices/{id}/issue`
- `GET` and `PUT` on `/settings`

Permissions: `invoicing.view`, `invoicing.draft`, `invoicing.issue`, and `invoicing.settings`.

**Done when:** tests cover:

- an issued invoice whose entry is balanced and shows on the trial balance
- an invoice dated on the lock date, which stays a draft with no entry and no number used
- issuing twice
- missing settings
- the dependency rules: Accounting cannot be removed while Invoicing is installed
- property-based tests on random lines, which show the entry always balances and always equals the invoice total
- all of the above run against the oldest and the newest Accounting version in the range

### Task 5.4: Invoicing screens and first release

Repository: `bowerstone-invoicing`.

- **Screens:**
  - Customers, from the shared list
  - Invoices: list, new invoice, and issue
  - Settings: pick the two accounts
- **Release.** Ilia tags `v0.1.0`.

**Done when:** component tests pass, and a Playwright test sets the accounts, adds a customer, and issues an invoice.

## Phase 6: The Docker image and end-to-end tests

All tasks in this phase are in `bowerstone-platform`.

### Task 6.1: Docker

- **`distribution.toml`** lists the apps and versions the image includes by default: Accounting and Invoicing.
- **The roles script** from task 0.2 runs on the first start of the image's database too.
- **`compose.yaml`** runs three services:
  - `db`: Postgres 18 with the built-in locale, its volume at `/var/lib/postgresql`
  - `migrate`: runs once as the owner role, and installs the default apps the first time
  - `app`: the engine as the app role, serving the shell
- **The `Dockerfile`:**
  - It builds the shell, then installs the engine and the listed app packages from PyPI.
  - It pins `uv`, the base images, and every package version.
  - It runs as a non-root user.

**Done when:** `docker compose up` on a clean computer leads to a working app with both apps, the volume path is confirmed for Postgres 18, and `bowerstone apps list` runs inside the container.

### Task 6.2: End-to-end test

A Playwright test runs against the Docker image with the released apps:

1. Create the accounts.
2. Post an entry.
3. Set the invoicing accounts.
4. Add a customer.
5. Issue an invoice.
6. Check the trial balance.

The test also removes Invoicing and restarts the engine, checks that its screens disappear and the books are unchanged, then reinstalls it, restarts again, and checks that its invoices are back.

**Done when:** the test passes in the automated build.

### Task 6.3: Automated builds in every repository

| Repository | Jobs |
| --- | --- |
| `bowerstone-platform` | Everything runs through `compose.dev.yaml` and the `make` targets, as on the development computer. Engine: the compose `db` container, `ruff`, `mypy`, `pytest`, `bandit`, `pip-audit`. Web: type check, `eslint`, unit tests, build, API types current, `npm audit`. End-to-end test. Guard hook tests and `gitleaks` |
| Each app repository | The template's jobs (task 3.2), plus the contract tests against the oldest and newest version of each app it needs |

**Done when:** a pull request in each repository shows every job green. A deliberately broken ledger rule test turns the Accounting build red.

## Phase 7, later: the launcher

This phase is the desktop phase in the Charter, decision 5. It gets its own repository, `bowerstone-launcher`.

- **What is built.** The launcher is a Tauri 2 app.
  - It runs Postgres through `postgresql_embedded`, and the engine through python-build-standalone.
  - It installs signed app packages from GitHub releases.
- **The contract.** It works through the `bowerstone` commands from task 1.6. The platform README records that contract, and keeps it current as the commands change.

## Dependencies for Ilia to approve

Rule 6 needs approval for each of these. Each one is added in the task that first needs it. Versions are the latest stable releases at that time, pinned by the lock files.

| Package | Used for | Licence |
| --- | --- | --- |
| fastapi | The API | MIT |
| uvicorn | Runs the API | BSD-3-Clause |
| sqlalchemy | Database access | MIT |
| psycopg | The Postgres driver | LGPL-3.0 |
| pydantic-settings | Settings | MIT |
| hatchling | Builds the Python packages | MIT |
| pytest, httpx, hypothesis | Tests | MIT, BSD-3-Clause, MPL-2.0 |
| ruff, mypy | Lint and types | MIT, MIT |
| bandit, pip-audit | Security checks | Apache-2.0, Apache-2.0 |
| react, react-dom | Screens | MIT |
| @tanstack/react-query | Loading data in the screens | MIT |
| decimal.js | Money in the screens | MIT |
| vite, @vitejs/plugin-react, typescript | Building the screens | MIT, MIT, Apache-2.0 |
| vitest, @testing-library/react, jsdom | Screen tests | MIT |
| @playwright/test | End-to-end tests | Apache-2.0 |
| openapi-typescript | API types for the screens | MIT |
| eslint | TypeScript lint | MIT |
| gitleaks | Secret scanning in the automated builds | MIT |

## After the base

The next plans, in order:

1. Sign-in, users, and roles.
2. Backup, restore, and export, including the backup file used to move to the web version.
3. The CRM app, in `bowerstone-crm`.
4. Sales tax, once the accounting rules are confirmed.
5. Hosted operations and the control plane, in `bowerstone-cloud`.
6. The launcher, in `bowerstone-launcher`.
