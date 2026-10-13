"""Tests for bowerstone_engine.migrations.migrate.upgrade against PostgreSQL 18."""

import hashlib
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from psycopg import errors as postgres_errors
from sqlalchemy import Connection, Engine, Row, exc, text

from bowerstone_engine.errors import MigrationError
from bowerstone_engine.migration_runner.migrate import upgrade
from bowerstone_engine.migration_runner.migration_files import Migration, read_migrations

AddMigration = Callable[..., None]
ReadDemo = Callable[[], list[Migration]]
Query = Callable[..., Sequence[Row[Any]]]
Versions = Callable[[str], list[int]]


def test_a_fresh_upgrade_applies_every_migration(
    three_tables: list[Migration], query: Query, applied_versions: Versions
) -> None:
    """Each file runs once, in order, and is recorded with its checksum."""
    assert applied_versions("demo") == [1, 2, 3]
    rows = query(
        "SELECT name, checksum FROM core.applied_migration "
        "WHERE module = 'demo' ORDER BY version"
    )
    assert [(row.name, row.checksum) for row in rows] == [
        (m.name, m.checksum) for m in three_tables
    ]
    assert query("SELECT to_regclass('demo.b') IS NOT NULL AS exists")[0].exists


def test_a_second_run_changes_nothing(
    three_tables: list[Migration], migration_connection: Connection, query: Query
) -> None:
    """Running again applies nothing and leaves the records as they were."""
    before = query("SELECT version, applied_at FROM core.applied_migration")
    assert upgrade(migration_connection, "demo", three_tables) == []
    assert query("SELECT version, applied_at FROM core.applied_migration") == before


def test_a_new_file_is_applied_after_the_old_ones(
    three_tables: list[Migration],
    add_migration: AddMigration,
    demo_migrations: ReadDemo,
    migration_connection: Connection,
    applied_versions: Versions,
) -> None:
    """A later release adds a file, and only that file runs."""
    add_migration(4, "third", "CREATE TABLE demo.c (x int);\n", "DROP TABLE demo.c;\n")
    applied = upgrade(migration_connection, "demo", demo_migrations())
    assert [m.version for m in applied] == [4]
    assert applied_versions("demo") == [1, 2, 3, 4]


def test_an_edited_file_is_refused_and_nothing_new_runs(
    three_tables: list[Migration],
    migrations_folder: Path,
    add_migration: AddMigration,
    demo_migrations: ReadDemo,
    migration_connection: Connection,
    applied_versions: Versions,
) -> None:
    """Changing a released file is refused, even by a comment, before anything runs."""
    edited = migrations_folder / "0002_first.sql"
    edited.write_bytes(edited.read_bytes() + b"-- an innocent comment\n")
    add_migration(4, "third", "CREATE TABLE demo.c (x int);\n", "DROP TABLE demo.c;\n")
    with pytest.raises(
        MigrationError, match=re.escape("0002_first.sql changed after it was applied")
    ):
        upgrade(migration_connection, "demo", demo_migrations())
    assert applied_versions("demo") == [1, 2, 3]


def test_a_renamed_file_is_refused(
    three_tables: list[Migration],
    migrations_folder: Path,
    demo_migrations: ReadDemo,
    migration_connection: Connection,
) -> None:
    """A file renamed after it was applied no longer matches its record."""
    for suffix in (".sql", ".down.sql"):
        (migrations_folder / f"0003_second{suffix}").rename(
            migrations_folder / f"0003_renamed{suffix}"
        )
    with pytest.raises(
        MigrationError, match="applied as second, but its file is now named"
    ):
        upgrade(migration_connection, "demo", demo_migrations())


def test_a_database_ahead_of_the_code_is_refused(
    three_tables: list[Migration], migration_connection: Connection
) -> None:
    """Older code must not run against a database that newer code has migrated."""
    with pytest.raises(
        MigrationError, match="has migration 0003_second, which this version"
    ):
        upgrade(migration_connection, "demo", three_tables[:2])


def test_a_gap_in_the_records_is_refused(
    three_tables: list[Migration], owner_pool: Engine, migration_connection: Connection
) -> None:
    """A record removed by hand is noticed rather than the file run a second time."""
    with owner_pool.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM core.applied_migration "
                "WHERE module = 'demo' AND version = 1"
            )
        )
    with pytest.raises(MigrationError, match="no record of migration 0001"):
        upgrade(migration_connection, "demo", three_tables)


def test_the_files_given_must_run_from_0001(
    three_tables: list[Migration], migration_connection: Connection
) -> None:
    """A caller passing a slice of the migrations is refused, not trusted."""
    with pytest.raises(MigrationError, match="do not run from 0001"):
        upgrade(migration_connection, "demo", three_tables[1:])


def test_a_failing_file_leaves_nothing_behind(
    add_migration: AddMigration,
    demo_migrations: ReadDemo,
    migration_connection: Connection,
    query: Query,
    applied_versions: Versions,
) -> None:
    """A file that fails partway is rolled back with its record. Earlier files stay."""
    add_migration(1, "schema", "CREATE SCHEMA demo;\n", "DROP SCHEMA demo;\n")
    add_migration(
        2, "broken", "CREATE TABLE demo.b (x int);\nSELECT 1 / 0;\n", "SELECT 1;\n"
    )
    with pytest.raises(
        MigrationError, match=re.escape("0002_broken.sql failed: division by zero")
    ):
        upgrade(migration_connection, "demo", demo_migrations())
    assert applied_versions("demo") == [1]
    assert query("SELECT to_regclass('demo.b') IS NULL AS gone")[0].gone


def test_percent_signs_reach_postgresql_unchanged(
    add_migration: AddMigration,
    demo_migrations: ReadDemo,
    migration_connection: Connection,
    owner_pool: Engine,
) -> None:
    """The ledger's error messages use %, so a file's % signs must not be touched."""
    function = (
        "CREATE SCHEMA demo;\n"
        "CREATE FUNCTION demo.check_positive(v integer) RETURNS void\n"
        "LANGUAGE plpgsql AS $$\nBEGIN\n"
        "    IF v <= 0 THEN\n"
        "        RAISE EXCEPTION 'value % is not positive (100%% sure)', v;\n"
        "    END IF;\n"
        "END $$;\n"
    )
    add_migration(1, "function", function, "DROP SCHEMA demo CASCADE;\n")
    upgrade(migration_connection, "demo", demo_migrations())
    with (
        owner_pool.connect() as connection,
        pytest.raises(exc.ProgrammingError) as raised,
    ):
        connection.execute(text("SELECT demo.check_positive(-3)"))
    assert isinstance(raised.value.orig, postgres_errors.RaiseException)
    assert "value -3 is not positive (100% sure)" in str(raised.value.orig)


def test_records_are_kept_apart_per_module(
    three_tables: list[Migration],
    tmp_path: Path,
    migration_connection: Connection,
    applied_versions: Versions,
) -> None:
    """Each module has its own numbering, and its records never touch another's."""
    other = tmp_path / "other"
    other.mkdir()
    (other / "0001_other.sql").write_bytes(b"CREATE SCHEMA other;\n")
    (other / "0001_other.down.sql").write_bytes(b"DROP SCHEMA other;\n")
    assert (
        len(upgrade(migration_connection, "other", read_migrations("other", other)))
        == 1
    )
    assert applied_versions("other") == [1]
    assert applied_versions("demo") == [1, 2, 3]


def test_the_record_table_is_made_once_and_owned_by_the_owner(
    migration_connection: Connection, query: Query
) -> None:
    """With no migrations, a run still creates core.applied_migration, as the owner."""
    assert upgrade(migration_connection, "demo", []) == []
    owner = query(
        "SELECT tableowner FROM pg_tables "
        "WHERE schemaname = 'core' AND tablename = 'applied_migration'"
    )
    assert [row.tableowner for row in owner] == ["bowerstone_owner"]


def test_a_record_must_hold_a_real_checksum(
    migration_connection: Connection, owner_pool: Engine
) -> None:
    """The table itself refuses a version out of range or a malformed checksum."""
    upgrade(migration_connection, "demo", [])
    good = hashlib.sha256(b"x").hexdigest()
    for version, checksum in [(0, good), (10_000, good), (1, "not-a-checksum")]:
        with owner_pool.begin() as connection, pytest.raises(exc.IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO core.applied_migration "
                    "(module, version, name, checksum) "
                    "VALUES ('demo', :version, 'x', :checksum)"
                ),
                {"version": version, "checksum": checksum},
            )


def test_a_connection_inside_a_transaction_is_refused(
    migration_connection: Connection,
) -> None:
    """The runner manages its own transactions, so it refuses one already open."""
    migration_connection.begin()
    with pytest.raises(MigrationError, match="no open transaction"):
        upgrade(migration_connection, "demo", [])
    migration_connection.rollback()
