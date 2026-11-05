"""The bowerstone command, run as `bowerstone` and as `python -m bowerstone_engine`,
in a separate process, against the test databases.

Each test runs once for each way of starting the command. A process gets only
the BOWERSTONE_ settings its test gives it.
"""

import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx2
import pytest
import sqlalchemy
from sqlalchemy import Engine, text
from sqlalchemy.pool import NullPool

from bowerstone_engine.db import APP_ROLE, OWNER_ROLE
from bowerstone_engine.installer import install, uninstall
from bowerstone_engine.migrate import core_migrations, upgrade
from bowerstone_engine.testing import PostgresServer, ScratchDatabase

COMMANDS = {
    "bowerstone": [str(Path(sys.executable).parent / "bowerstone")],
    "python -m bowerstone_engine": [sys.executable, "-m", "bowerstone_engine"],
}
TAKES_EFFECT = "This takes effect when the engine next starts."


@dataclass(frozen=True)
class Command:
    """Runs the bowerstone command in its own process."""

    argv: list[str]

    def __call__(
        self, *args: str, env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - the test's own command
            [*self.argv, *args],
            env={**clean_environment(), **(env or {})},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    def start(self, *args: str, env: Mapping[str, str]) -> subprocess.Popen[str]:
        return subprocess.Popen(  # noqa: S603 - the test's own command
            [*self.argv, *args],
            env={**clean_environment(), **env},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )


def clean_environment() -> dict[str, str]:
    return {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("BOWERSTONE_")
    }


@pytest.fixture(params=list(COMMANDS))
def bowerstone(request: pytest.FixtureRequest) -> Command:
    """The command, started one way or the other."""
    return Command(COMMANDS[request.param])


def settings_for(database: ScratchDatabase) -> dict[str, str]:
    return {
        "BOWERSTONE_ENV": "development",
        "BOWERSTONE_OWNER_DATABASE_URL": database.url(OWNER_ROLE).render_as_string(
            hide_password=False
        ),
        "BOWERSTONE_DATABASE_URL": database.url(APP_ROLE).render_as_string(
            hide_password=False
        ),
    }


def assert_fails_with(result: subprocess.CompletedProcess[str], message: str) -> None:
    """The command failed with status 1 and one plain sentence, never a stack trace."""
    assert result.returncode == 1, result.stderr
    assert message in result.stderr
    assert len(result.stderr.strip().splitlines()) == 1, result.stderr
    assert "Traceback" not in result.stderr


def product_table(output: str) -> dict[str, str]:
    """The `products list` output, as each product's key and state."""
    lines = output.strip().splitlines()
    assert lines[0].split() == ["KEY", "NAME", "VERSION", "STATE"]
    return {line.split()[0]: " ".join(line.split()[3:]) for line in lines[1:]}


# Help and mistakes.


def test_help_lists_every_command(bowerstone: Command) -> None:
    result = bowerstone("--help")

    assert result.returncode == 0
    for command in ("setup-database", "migrate", "products", "serve", "check-imports"):
        assert command in result.stdout


def test_a_usage_mistake_is_one_sentence_and_status_1(bowerstone: Command) -> None:
    assert_fails_with(
        bowerstone("products", "install"), "the following arguments are required: key"
    )
    assert_fails_with(bowerstone("unknown"), "invalid choice: 'unknown'")


def test_a_missing_setting_is_named(bowerstone: Command) -> None:
    assert_fails_with(
        bowerstone("migrate"), "BOWERSTONE_OWNER_DATABASE_URL is not set."
    )


def test_an_unreachable_database_is_one_sentence(bowerstone: Command) -> None:
    unreachable = "postgresql://bowerstone_owner:secret-password@127.0.0.1:1/bowerstone"
    result = bowerstone("migrate", env={"BOWERSTONE_OWNER_DATABASE_URL": unreachable})

    assert_fails_with(result, "Cannot reach the database: ")
    assert "secret-password" not in result.stderr


# setup-database.


@pytest.fixture
def hidden_roles(server: PostgresServer) -> Iterator[Engine]:
    """Hides the test server's two roles by renaming them, and puts them back
    afterwards, so setup-database finds a server without them."""
    admin = sqlalchemy.create_engine(
        server.admin_url, poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as conn:
        for role in (OWNER_ROLE, APP_ROLE):
            conn.execute(text(f"ALTER ROLE {role} RENAME TO {role}_hidden"))
    try:
        yield admin
    finally:
        with admin.connect() as conn:
            for role in (OWNER_ROLE, APP_ROLE):
                conn.execute(text(f"DROP ROLE IF EXISTS {role}"))
                conn.execute(text(f"ALTER ROLE {role}_hidden RENAME TO {role}"))
        admin.dispose()


def test_setup_database_creates_the_roles_and_the_database(
    bowerstone: Command, server: PostgresServer, hidden_roles: Engine
) -> None:
    name = f"bw_test_setup_{secrets.token_hex(4)}"
    owner_password, app_password = "owner pass % 'quoted'", "app pass % 'quoted'"
    owner = server.admin_url.set(
        username=OWNER_ROLE, password=owner_password, database=name
    )
    app = server.admin_url.set(username=APP_ROLE, password=app_password, database=name)
    env = {
        "BOWERSTONE_OWNER_DATABASE_URL": owner.render_as_string(hide_password=False),
        "BOWERSTONE_DATABASE_URL": app.render_as_string(hide_password=False),
    }
    admin_url = server.admin_url.render_as_string(hide_password=False)
    try:
        first = bowerstone("setup-database", "--admin-url", admin_url, env=env)
        again = bowerstone(
            "setup-database", env={**env, "BOWERSTONE_ADMIN_DATABASE_URL": admin_url}
        )

        assert first.returncode == 0, first.stderr
        assert first.stdout.splitlines() == [
            f"Created the role {OWNER_ROLE}.",
            f"Created the role {APP_ROLE}.",
            f"Created the database {name}.",
            "The database is ready for bowerstone migrate.",
        ]
        assert again.returncode == 0, again.stderr
        assert again.stdout.splitlines() == [
            f"The role {OWNER_ROLE} already exists, so it was left as it is.",
            f"The role {APP_ROLE} already exists, so it was left as it is.",
            f"The database {name} already exists, so it was left as it is.",
            "The database is ready for bowerstone migrate.",
        ]

        # The roles sign in with their passwords, and migrations can run.
        migrate = bowerstone("migrate", env=env)
        assert migrate.stdout.splitlines()[0] == "Applied core 0001_core.", (
            migrate.stderr
        )
        app_engine = sqlalchemy.create_engine(
            app.set(drivername="postgresql+psycopg"), poolclass=NullPool
        )
        with app_engine.connect() as conn:
            assert conn.execute(text("SELECT current_user")).scalar_one() == APP_ROLE
        app_engine.dispose()
    finally:
        with hidden_roles.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def test_setup_database_needs_an_admin_address(bowerstone: Command) -> None:
    assert_fails_with(
        bowerstone("setup-database"), "Give the admin's address with --admin-url"
    )


# migrate.


def test_migrate_applies_and_prints_each_migration(
    bowerstone: Command, database: ScratchDatabase
) -> None:
    first = bowerstone("migrate", env=settings_for(database))
    again = bowerstone("migrate", env=settings_for(database))

    assert (first.returncode, first.stdout) == (
        0,
        "Applied core 0001_core.\nThe database is up to date.\n",
    )
    assert (again.returncode, again.stdout) == (0, "The database is up to date.\n")


def test_migrate_refuses_a_database_that_fails_the_check(
    bowerstone: Command, server_pg17: PostgresServer
) -> None:
    with server_pg17.scratch_database() as database:
        result = bowerstone("migrate", env=settings_for(database))

    assert_fails_with(
        result,
        "The database server runs PostgreSQL 17, and Bowerstone needs PostgreSQL 18.",
    )


# products.


def test_products_list_shows_each_products_state(
    bowerstone: Command, database: ScratchDatabase, owner: Engine
) -> None:
    before = bowerstone("products", "list", env=settings_for(database))
    install(owner, "tasks")
    uninstall(owner, "tasks")
    after = bowerstone("products", "list", env=settings_for(database))

    assert before.returncode == 0, before.stderr
    assert product_table(before.stdout) == {"notes": "available", "tasks": "available"}
    assert product_table(after.stdout) == {"notes": "installed", "tasks": "removed"}


def test_products_install_installs_what_a_product_needs(
    bowerstone: Command, database: ScratchDatabase
) -> None:
    first = bowerstone("products", "install", "tasks", env=settings_for(database))
    again = bowerstone("products", "install", "tasks", env=settings_for(database))

    assert (first.returncode, first.stdout) == (
        0,
        f"Installed notes and tasks. {TAKES_EFFECT}\n",
    )
    assert (again.returncode, again.stdout) == (
        0,
        "The product tasks is already installed.\n",
    )
    assert_fails_with(
        bowerstone("products", "install", "invoices", env=settings_for(database)),
        "The product invoices's package is not installed.",
    )


def test_products_uninstall_keeps_or_deletes_data_as_told(
    bowerstone: Command, database: ScratchDatabase, owner: Engine
) -> None:
    install(owner, "tasks")
    env = settings_for(database)

    assert_fails_with(
        bowerstone("products", "uninstall", "notes", env=env),
        "The product notes cannot be removed while tasks needs it.",
    )
    removed = bowerstone("products", "uninstall", "tasks", env=env)
    assert (removed.returncode, removed.stdout) == (
        0,
        "Removed tasks. Its data is kept, so installing it again restores it. "
        f"{TAKES_EFFECT}\n",
    )
    assert_fails_with(
        bowerstone("products", "uninstall", "tasks", "--delete-data", env=env),
        "The data of the product tasks may never be deleted.",
    )
    deleted = bowerstone("products", "uninstall", "notes", "--delete-data", env=env)
    assert (deleted.returncode, deleted.stdout) == (
        0,
        f"Removed notes and deleted its data. {TAKES_EFFECT}\n",
    )


# serve.


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def test_serve_answers_once_the_database_check_passes(
    bowerstone: Command, database: ScratchDatabase, owner: Engine
) -> None:
    upgrade(owner, [core_migrations()])
    port = free_port()
    server = bowerstone.start("serve", "--port", str(port), env=settings_for(database))
    try:
        health = None
        deadline = time.monotonic() + 30
        while health is None and time.monotonic() < deadline:
            try:
                health = httpx2.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=2)
            except httpx2.TransportError:
                assert server.poll() is None, server.communicate()[1]
                time.sleep(0.2)
        assert health is not None, "the engine did not start"
        assert health.json() == {"status": "ok"}
        page = httpx2.get(f"http://127.0.0.1:{port}/", timeout=2)
        assert "The engine is running." in page.text
        assert "server" not in page.headers
    finally:
        server.terminate()
        _, errors = server.communicate(timeout=30)

    # uvicorn shuts down gracefully on SIGTERM, then re-raises the signal, so the
    # process reports that SIGTERM stopped it, as a Unix program should.
    assert server.returncode == -signal.SIGTERM, errors
    assert "Application shutdown complete." in errors
    assert "Traceback" not in errors


def test_serve_refuses_a_database_that_fails_the_check(
    bowerstone: Command, database: ScratchDatabase
) -> None:
    result = bowerstone("serve", "--port", str(free_port()), env=settings_for(database))

    assert_fails_with(result, "This migration has not been applied: core 0001_core.")


# check-imports.


def test_check_imports_passes_a_correct_product(bowerstone: Command) -> None:
    result = bowerstone("check-imports", "bowerstone_example_tasks")

    assert (result.returncode, result.stdout) == (
        0,
        "No import in bowerstone_example_tasks breaks the rules.\n",
    )
    assert_fails_with(
        bowerstone("check-imports", "sqlalchemy"),
        "sqlalchemy is not the package of any installed product.",
    )


def test_check_imports_reports_each_problem(
    bowerstone: Command, tmp_path: Path
) -> None:
    # A product installed the way pip installs one: a package, and its metadata
    # with the entry point.
    package = tmp_path / "bowerstone_bad"
    (package / "migrations").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "migrations" / "__init__.py").write_text("")
    (package / "manifest.py").write_text(
        "from bowerstone_engine.products import ProductManifest\n"
        "PRODUCT = ProductManifest(key='bad', name='Bad', version='0.1.0', "
        "migrations_package='bowerstone_bad.migrations', engine_versions='>=0.0', "
        "ui_versions='>=0.0', requires=('notes',))\n"
    )
    (package / "routes.py").write_text(
        "from bowerstone_example_notes.public import note_exists\n"
        "from bowerstone_engine.db import set_current_user\n"
    )
    info = tmp_path / "bowerstone_bad-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: bowerstone-bad\nVersion: 0.1.0\n"
    )
    (info / "entry_points.txt").write_text(
        "[bowerstone.products]\nbad = bowerstone_bad.manifest:PRODUCT\n"
    )

    result = bowerstone(
        "check-imports", "bowerstone_bad", env={"PYTHONPATH": str(tmp_path)}
    )

    assert result.returncode == 1
    assert result.stdout == (
        "bowerstone_bad/routes.py:2: imports bowerstone_engine.db, which is not one of "
        "the engine's public modules: "
        "auth, core.public, deps, errors, products, types.\n"
    )
    assert result.stderr == "1 import breaks the rules.\n"
