"""Tests for bowerstone_engine.migrations.migrate.downgrade against PostgreSQL 18."""

import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Connection, Row

from bowerstone_engine.errors import MigrationError
from bowerstone_engine.migration_runner.migrate import downgrade, upgrade
from bowerstone_engine.migration_runner.migration_files import Migration

AddMigration = Callable[..., None]
ReadDemo = Callable[[], list[Migration]]
Query = Callable[..., Sequence[Row[Any]]]
Versions = Callable[[str], list[int]]


def table_exists(query: Query, name: str) -> bool:
    """Say whether a table exists in this test's database.

    Args:
        query: The query fixture.
        name: The table's qualified name, such as demo.a.

    Returns:
        True when the table exists.

    Example:
        >>> table_exists(query, "demo.a")
        True
    """
    return bool(
        query("SELECT to_regclass(:name) IS NOT NULL AS found", name=name)[0].found
    )


def test_a_downgrade_undoes_the_newest_first(
    three_tables: list[Migration],
    migration_connection: Connection,
    query: Query,
    applied_versions: Versions,
) -> None:
    """Downgrading to 1 runs the down files of 3, then 2, and drops their records."""
    undone = downgrade(migration_connection, "demo", three_tables, 1)
    assert [m.version for m in undone] == [3, 2]
    assert applied_versions("demo") == [1]
    assert not table_exists(query, "demo.a")
    assert not table_exists(query, "demo.b")
    assert (
        query("SELECT count(*) AS n FROM pg_namespace WHERE nspname = 'demo'")[0].n == 1
    )


def test_a_downgrade_to_zero_undoes_everything(
    three_tables: list[Migration],
    migration_connection: Connection,
    query: Query,
    applied_versions: Versions,
) -> None:
    """Version 0 removes all of a module's migrations, as deleting app data does."""
    undone = downgrade(migration_connection, "demo", three_tables, 0)
    assert [m.version for m in undone] == [3, 2, 1]
    assert applied_versions("demo") == []
    assert (
        query("SELECT count(*) AS n FROM pg_namespace WHERE nspname = 'demo'")[0].n == 0
    )


def test_upgrading_after_a_downgrade_applies_the_files_again(
    three_tables: list[Migration],
    migration_connection: Connection,
    applied_versions: Versions,
) -> None:
    """A downgrade followed by an upgrade puts the module back as it was."""
    downgrade(migration_connection, "demo", three_tables, 1)
    assert [m.version for m in upgrade(migration_connection, "demo", three_tables)] == [
        2,
        3,
    ]
    assert applied_versions("demo") == [1, 2, 3]


@pytest.mark.parametrize("to_version", [3, 4])
def test_a_downgrade_to_the_current_version_or_above_does_nothing(
    three_tables: list[Migration],
    add_migration: AddMigration,
    demo_migrations: ReadDemo,
    migration_connection: Connection,
    applied_versions: Versions,
    to_version: int,
) -> None:
    """Nothing applied above the target, so nothing is undone."""
    add_migration(4, "later", "CREATE TABLE demo.c (x int);\n", "DROP TABLE demo.c;\n")
    assert downgrade(migration_connection, "demo", demo_migrations(), to_version) == []
    assert applied_versions("demo") == [1, 2, 3]


@pytest.mark.parametrize("to_version", [-1, 4, True])
def test_a_target_out_of_range_is_refused(
    three_tables: list[Migration], migration_connection: Connection, to_version: int
) -> None:
    """The target must be a whole number from 0 to the number of migrations."""
    with pytest.raises(MigrationError, match="cannot downgrade to"):
        downgrade(migration_connection, "demo", three_tables, to_version)


def test_a_downgrade_refuses_an_edited_file(
    three_tables: list[Migration],
    migrations_folder: Path,
    demo_migrations: ReadDemo,
    migration_connection: Connection,
    applied_versions: Versions,
) -> None:
    """The checks upgrade makes run first, so nothing is undone against wrong files."""
    edited = migrations_folder / "0003_second.sql"
    edited.write_bytes(b"CREATE TABLE demo.b (x bigint);\n")
    with pytest.raises(MigrationError, match="changed after it was applied"):
        downgrade(migration_connection, "demo", demo_migrations(), 0)
    assert applied_versions("demo") == [1, 2, 3]


def test_a_failing_down_file_keeps_its_record(
    add_migration: AddMigration,
    demo_migrations: ReadDemo,
    migration_connection: Connection,
    query: Query,
    applied_versions: Versions,
) -> None:
    """A failing down file is rolled back, so its migration still counts as applied."""
    add_migration(1, "schema", "CREATE SCHEMA demo;\n", "DROP SCHEMA demo;\n")
    add_migration(
        2,
        "stubborn",
        "CREATE TABLE demo.a (x int);\n",
        "DROP TABLE demo.a;\nSELECT 1 / 0;\n",
    )
    migrations = demo_migrations()
    upgrade(migration_connection, "demo", migrations)
    with pytest.raises(
        MigrationError, match=re.escape("0002_stubborn.down.sql failed")
    ):
        downgrade(migration_connection, "demo", migrations, 0)
    assert applied_versions("demo") == [1, 2]
    assert table_exists(query, "demo.a")
