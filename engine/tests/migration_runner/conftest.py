"""Fixtures for the migration runner's tests.

Migrations commit, so each test gets a database of its own, made from the
shared fixtures in engine/tests/conftest.py, and a folder to write files into.
"""

from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import Connection, Engine, Row, text

from bowerstone_engine.migration_runner.migrate import upgrade
from bowerstone_engine.migration_runner.migration_files import Migration, read_migrations

AddMigration = Callable[..., None]
Query = Callable[..., Sequence[Row[Any]]]


@pytest.fixture
def fresh_owner_url(
    database_factory: Callable[[str], str],
    owner_url_for: Callable[[str], SecretStr],
) -> SecretStr:
    """The owner role's address for a database made for this test alone."""
    return owner_url_for(database_factory("builtin"))


@pytest.fixture
def owner_pool(
    fresh_owner_url: SecretStr, pool_factory: Callable[..., Engine]
) -> Engine:
    """Connections to this test's database as the owner role."""
    return pool_factory(fresh_owner_url, pool_size=3)


@pytest.fixture
def migration_connection(owner_pool: Engine) -> Iterator[Connection]:
    """One connection as the owner role, with no open transaction."""
    with owner_pool.connect() as connection:
        yield connection


@pytest.fixture
def migrations_folder(tmp_path: Path) -> Path:
    """An empty migrations folder for this test."""
    folder = tmp_path / "migrations"
    folder.mkdir()
    return folder


@pytest.fixture
def add_migration(migrations_folder: Path) -> AddMigration:
    """Write a migration's upgrade and down files into this test's folder."""

    def add(version: int, name: str, upgrade_sql: str, down_sql: str) -> None:
        """Write NNNN_name.sql and NNNN_name.down.sql with the given SQL."""
        stem = f"{version:04d}_{name}"
        (migrations_folder / f"{stem}.sql").write_bytes(upgrade_sql.encode("utf-8"))
        (migrations_folder / f"{stem}.down.sql").write_bytes(down_sql.encode("utf-8"))

    return add


@pytest.fixture
def demo_migrations(migrations_folder: Path) -> Callable[[], list[Migration]]:
    """Read this test's folder as the migrations of a module called demo."""
    return lambda: read_migrations("demo", migrations_folder)


@pytest.fixture
def query(owner_pool: Engine) -> Query:
    """Run a read-only query on this test's database, on a connection of its own."""

    def run(sql: str, **values: Any) -> Sequence[Row[Any]]:
        """Return every row the query gives."""
        with owner_pool.connect() as connection:
            return connection.execute(text(sql), values).all()

    return run


@pytest.fixture
def applied_versions(query: Query) -> Callable[[str], list[int]]:
    """Read which versions of a module core.applied_migration records."""
    return lambda module: [
        row.version
        for row in query(
            "SELECT version FROM core.applied_migration "
            "WHERE module = :module ORDER BY version",
            module=module,
        )
    ]


@pytest.fixture
def three_tables(
    add_migration: AddMigration,
    demo_migrations: Callable[[], list[Migration]],
    migration_connection: Connection,
) -> list[Migration]:
    """A demo module with three migrations, all applied: a schema and two tables."""
    add_migration(1, "schema", "CREATE SCHEMA demo;\n", "DROP SCHEMA demo;\n")
    add_migration(2, "first", "CREATE TABLE demo.a (x int);\n", "DROP TABLE demo.a;\n")
    add_migration(3, "second", "CREATE TABLE demo.b (x int);\n", "DROP TABLE demo.b;\n")
    migrations = demo_migrations()
    upgrade(migration_connection, "demo", migrations)
    return migrations
