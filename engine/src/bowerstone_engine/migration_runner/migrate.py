"""The migration runner: applies each module's migrations once, in order (task 1.2).

Every run holds the migration lock on its one connection, compares the records
in core.applied_migration with the module's files, and refuses to continue when
they disagree. Upgrades then apply the files the database does not have yet.
Downgrades undo applied files, newest first, with their down files. Each file
runs in one transaction with its record, except no-transaction files, which run
alone and are recorded after they succeed.

The database check (bowerstone_engine.database_check) runs before any of this,
in upgrade_all (task 1.4).
"""

from collections.abc import Sequence

from sqlalchemy import Connection

from bowerstone_engine.errors import MigrationError
from bowerstone_engine.migration_runner.migration_execution import (
    migration_lock,
    run_with_record,
)
from bowerstone_engine.migration_runner.migration_files import (
    Migration,
    check_module_name,
)
from bowerstone_engine.migration_runner.migration_records import (
    ADD_RECORD,
    REMOVE_RECORD,
    check_records,
    read_records,
)


def upgrade(
    connection: Connection, module: str, migrations: Sequence[Migration]
) -> list[Migration]:
    """Apply every migration of a module that the database does not have yet.

    Args:
        connection: A connection as bowerstone_owner, with no open transaction.
        module: The module's name, such as core.
        migrations: The module's migrations, from read_migrations.

    Returns:
        The migrations applied by this run, in order. Empty if none were new.

    Raises:
        MigrationError: If the records and files disagree, or a file fails to
            run. Files before the failing one stay applied, and the failing one
            leaves nothing behind.

    Example:
        >>> upgrade(connection, "core", read_migrations("core", folder))
    """
    check_module_name(module)
    with migration_lock(connection):
        records = read_records(connection, module)
        check_records(module, records, migrations)
        pending = list(migrations[len(records) :])
        for migration in pending:
            values = {
                "module": module,
                "version": migration.version,
                "name": migration.name,
                "checksum": migration.checksum,
            }
            run_with_record(connection, module, migration.upgrade, (ADD_RECORD, values))
    return pending


def downgrade(
    connection: Connection,
    module: str,
    migrations: Sequence[Migration],
    to_version: int,
) -> list[Migration]:
    """Undo a module's applied migrations above a version, newest first.

    Args:
        connection: A connection as bowerstone_owner, with no open transaction.
        module: The module's name, such as core.
        migrations: The module's migrations, from read_migrations.
        to_version: The version to keep, from 0 (undo everything) up.

    Returns:
        The migrations undone by this run, newest first.

    Raises:
        MigrationError: If to_version is out of range, the records and files
            disagree, or a down file fails to run.

    Example:
        >>> downgrade(connection, "core", read_migrations("core", folder), 0)
    """
    check_module_name(module)
    if isinstance(to_version, bool) or not 0 <= to_version <= len(migrations):
        raise MigrationError(
            f"{module}: cannot downgrade to {to_version!r}; "
            f"choose 0 to {len(migrations)}"
        )
    with migration_lock(connection):
        records = read_records(connection, module)
        check_records(module, records, migrations)
        undone = list(reversed(migrations[to_version : len(records)]))
        for migration in undone:
            values = {"module": module, "version": migration.version}
            run_with_record(
                connection, module, migration.downgrade, (REMOVE_RECORD, values)
            )
    return undone
