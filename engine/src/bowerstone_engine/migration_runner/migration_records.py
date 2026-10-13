"""The record of applied migrations, core.applied_migration (task 1.2).

The runner creates the core schema and this table itself when they are missing,
before the first core migration runs, so 0001_core.sql does not create them.
Each row holds a module, a version, a name, and the upgrade file's checksum. A
run refuses to continue when these rows and the files disagree.
"""

from collections.abc import Sequence
from typing import NamedTuple

from sqlalchemy import Connection, text

from bowerstone_engine.errors import MigrationError
from bowerstone_engine.migration_runner.migration_execution import driver_connection
from bowerstone_engine.migration_runner.migration_files import Migration

_CREATE_RECORD_TABLE = """
CREATE SCHEMA IF NOT EXISTS core;
CREATE TABLE IF NOT EXISTS core.applied_migration (
    module     text        NOT NULL,
    version    integer     NOT NULL CHECK (version BETWEEN 1 AND 9999),
    name       text        NOT NULL,
    checksum   text        NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (module, version)
);
"""
_READ_RECORDS = text(
    "SELECT version, name, checksum FROM core.applied_migration "
    "WHERE module = :module ORDER BY version"
)
ADD_RECORD = text(
    "INSERT INTO core.applied_migration (module, version, name, checksum) "
    "VALUES (:module, :version, :name, :checksum)"
)
REMOVE_RECORD = text(
    "DELETE FROM core.applied_migration WHERE module = :module AND version = :version"
)


class AppliedRecord(NamedTuple):
    """One row of core.applied_migration for a module.

    Attributes:
        version: The migration's number.
        name: The migration's name when it was applied.
        checksum: The upgrade file's SHA-256 when it was applied.
    """

    version: int
    name: str
    checksum: str


def read_records(connection: Connection, module: str) -> list[AppliedRecord]:
    """Create core.applied_migration if it is missing, then read a module's rows.

    Args:
        connection: The migration connection, holding the lock.
        module: The module whose records to read.

    Returns:
        The module's records, by version.

    Example:
        >>> [record.version for record in read_records(connection, "core")]
        [1, 2]
    """
    with connection.begin():
        with driver_connection(connection).cursor() as cursor:
            cursor.execute(_CREATE_RECORD_TABLE)
        rows = connection.execute(_READ_RECORDS, {"module": module}).all()
    return [AppliedRecord(*row) for row in rows]


def _check_order(module: str, records: Sequence[AppliedRecord], count: int) -> None:
    """Check that the records run from 0001 with no gaps and no more than the files.

    Args:
        module: The module the records belong to.
        records: The module's records, by version.
        count: How many migration files the module has.

    Raises:
        MigrationError: If the records have a gap, or the database has more
            migrations than the files.

    Example:
        >>> _check_order("core", records, 2)
    """
    for expected, record in enumerate(records, start=1):
        if record.version != expected:
            raise MigrationError(
                f"{module}: core.applied_migration has no record of migration "
                f"{expected:04d}, but has later ones"
            )
    if len(records) > count:
        newest = records[-1]
        raise MigrationError(
            f"{module}: the database has migration "
            f"{newest.version:04d}_{newest.name}, "
            "which this version of the code does not have"
        )


def check_records(
    module: str, records: Sequence[AppliedRecord], migrations: Sequence[Migration]
) -> None:
    """Refuse to continue when the database's records and the files disagree.

    Args:
        module: The module the records and files belong to.
        records: The module's records, by version.
        migrations: The module's migration files, from 0001.

    Raises:
        MigrationError: If the files do not run from 0001, the records have a
            gap, the database is ahead of the files, or an applied file was
            renamed or changed.

    Example:
        >>> check_records("core", records, read_migrations("core", folder))
    """
    if [m.version for m in migrations] != list(range(1, len(migrations) + 1)):
        raise MigrationError(
            f"{module}: the migrations given do not run from 0001 in order"
        )
    _check_order(module, records, len(migrations))
    for record, migration in zip(records, migrations, strict=False):
        if record.name != migration.name:
            raise MigrationError(
                f"{module}: migration {record.version:04d} was applied as "
                f"{record.name}, but its file is now named {migration.name}"
            )
        if record.checksum != migration.checksum:
            raise MigrationError(
                f"{module}: {migration.upgrade.file_name} changed after it was "
                "applied; add a new migration instead of editing a released one"
            )
