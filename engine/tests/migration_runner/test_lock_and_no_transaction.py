"""Tests for the migration runner's lock and its no-transaction files."""

import re
import threading
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import Connection, Engine, Row, text

from bowerstone_engine.errors import MigrationError
from bowerstone_engine.migration_runner.migrate import downgrade, upgrade
from bowerstone_engine.migration_runner.migration_execution import MIGRATION_LOCK_KEY
from bowerstone_engine.migration_runner.migration_files import (
    NO_TRANSACTION_MARKER,
    Migration,
)

AddMigration = Callable[..., None]
ReadDemo = Callable[[], list[Migration]]
Query = Callable[..., Sequence[Row[Any]]]
Versions = Callable[[str], list[int]]

INDEX_STATE = (
    "SELECT i.indisvalid AS valid FROM pg_index i "
    "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = 'a_x_idx'"
)


def test_two_runs_at_the_same_time_apply_each_migration_once(
    add_migration: AddMigration,
    demo_migrations: ReadDemo,
    owner_pool: Engine,
    applied_versions: Versions,
) -> None:
    """The second run waits for the lock, then finds nothing left to do."""
    add_migration(
        1, "slow", "CREATE SCHEMA demo;\nSELECT pg_sleep(0.5);\n", "DROP SCHEMA demo;\n"
    )
    add_migration(2, "table", "CREATE TABLE demo.a (x int);\n", "DROP TABLE demo.a;\n")
    migrations = demo_migrations()
    start_together = threading.Barrier(2, timeout=10)
    applied_counts: list[int] = []
    errors: list[BaseException] = []

    def run() -> None:
        """Start with the other thread, then upgrade on a connection of its own."""
        try:
            with owner_pool.connect() as connection:
                start_together.wait()
                applied_counts.append(len(upgrade(connection, "demo", migrations)))
        except (
            BaseException
        ) as error:  # handed to the main thread, which fails the test
            errors.append(error)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    assert sorted(applied_counts) == [0, 2]
    assert applied_versions("demo") == [1, 2]


def test_a_run_waiting_too_long_for_the_lock_gives_up(
    owner_pool: Engine,
    fresh_owner_url: SecretStr,
    pool_factory: Callable[..., Engine],
) -> None:
    """When another run holds the lock past the time limit, the waiting run says so."""
    impatient = pool_factory(fresh_owner_url, statement_timeout_ms=300)
    with owner_pool.connect() as holder:
        holder.execute(
            text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY}
        )
        holder.commit()
        with (
            impatient.connect() as connection,
            pytest.raises(MigrationError, match="another migration run held the lock"),
        ):
            upgrade(connection, "demo", [])
        holder.execute(
            text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY}
        )
        holder.commit()


def test_the_lock_is_released_when_a_run_fails(
    add_migration: AddMigration,
    demo_migrations: ReadDemo,
    owner_pool: Engine,
    fresh_owner_url: SecretStr,
    pool_factory: Callable[..., Engine],
) -> None:
    """After a failed run, the next run gets the lock at once instead of waiting."""
    add_migration(1, "broken", "SELECT 1 / 0;\n", "SELECT 1;\n")
    with owner_pool.connect() as connection, pytest.raises(MigrationError):
        upgrade(connection, "demo", demo_migrations())
    impatient = pool_factory(fresh_owner_url, statement_timeout_ms=300)
    with impatient.connect() as connection:
        assert upgrade(connection, "demo", []) == []


def test_a_no_transaction_file_creates_and_drops_an_index_concurrently(
    add_migration: AddMigration,
    demo_migrations: ReadDemo,
    migration_connection: Connection,
    query: Query,
    applied_versions: Versions,
) -> None:
    """CREATE and DROP INDEX CONCURRENTLY run outside a transaction and are recorded."""
    add_migration(
        1,
        "table",
        "CREATE SCHEMA demo;\nCREATE TABLE demo.a (x int);\n",
        "DROP SCHEMA demo CASCADE;\n",
    )
    add_migration(
        2,
        "index",
        f"{NO_TRANSACTION_MARKER}\nCREATE INDEX CONCURRENTLY a_x_idx ON demo.a (x);\n",
        f"{NO_TRANSACTION_MARKER}\nDROP INDEX CONCURRENTLY demo.a_x_idx;\n",
    )
    migrations = demo_migrations()
    upgrade(migration_connection, "demo", migrations)
    assert [row.valid for row in query(INDEX_STATE)] == [True]
    assert applied_versions("demo") == [1, 2]

    downgrade(migration_connection, "demo", migrations, 1)
    assert query(INDEX_STATE) == []
    assert applied_versions("demo") == [1]


def test_a_no_transaction_file_with_two_statements_is_refused(
    add_migration: AddMigration,
    demo_migrations: ReadDemo,
    migration_connection: Connection,
    query: Query,
    applied_versions: Versions,
) -> None:
    """PostgreSQL refuses a second statement: nothing runs, and nothing is recorded."""
    add_migration(
        1,
        "table",
        "CREATE SCHEMA demo;\nCREATE TABLE demo.a (x int);\n",
        "DROP SCHEMA demo CASCADE;\n",
    )
    add_migration(
        2,
        "index",
        f"{NO_TRANSACTION_MARKER}\n"
        "CREATE INDEX CONCURRENTLY a_x_idx ON demo.a (x);\nSELECT 1;\n",
        f"{NO_TRANSACTION_MARKER}\nDROP INDEX CONCURRENTLY demo.a_x_idx;\n",
    )
    with pytest.raises(MigrationError, match="may hold only one statement"):
        upgrade(migration_connection, "demo", demo_migrations())
    assert query(INDEX_STATE) == []
    assert applied_versions("demo") == [1]
    assert not migration_connection.in_transaction()


def test_normal_transactions_resume_after_a_no_transaction_file(
    add_migration: AddMigration,
    demo_migrations: ReadDemo,
    migration_connection: Connection,
    query: Query,
    applied_versions: Versions,
) -> None:
    """A failing file after a no-transaction one still rolls back as a whole."""
    add_migration(
        1,
        "table",
        "CREATE SCHEMA demo;\nCREATE TABLE demo.a (x int);\n",
        "DROP SCHEMA demo CASCADE;\n",
    )
    add_migration(
        2,
        "index",
        f"{NO_TRANSACTION_MARKER}\nCREATE INDEX CONCURRENTLY a_x_idx ON demo.a (x);\n",
        f"{NO_TRANSACTION_MARKER}\nDROP INDEX CONCURRENTLY demo.a_x_idx;\n",
    )
    add_migration(
        3, "broken", "CREATE TABLE demo.b (x int);\nSELECT 1 / 0;\n", "SELECT 1;\n"
    )
    with pytest.raises(MigrationError, match=re.escape("0003_broken.sql failed")):
        upgrade(migration_connection, "demo", demo_migrations())
    assert applied_versions("demo") == [1, 2]
    assert query("SELECT to_regclass('demo.b') IS NULL AS gone")[0].gone
