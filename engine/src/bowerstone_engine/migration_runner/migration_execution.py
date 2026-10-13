"""Running migration files, and the lock that lets one run at a time (task 1.2).

A run uses one connection, which holds a session-level advisory lock from start
to end, so two runs never change the schema at once. A lock that lasted one
transaction would not be enough, because each file has its own. A second run
waits, up to the migration statement timeout, then finds nothing left to do.

Files run through the database driver with no parameters, so one file may hold
many statements and its % signs are left alone.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import errors as postgres_errors
from sqlalchemy import Connection, TextClause, text
from sqlalchemy.exc import DBAPIError

from bowerstone_engine.errors import MigrationError
from bowerstone_engine.migration_runner.migration_files import MigrationScript

# Any fixed number would do. This one names the migration runner's advisory lock.
MIGRATION_LOCK_KEY = 4_206_310_172_001


def driver_connection(connection: Connection) -> Any:
    """Give the psycopg connection underneath a SQLAlchemy connection.

    Args:
        connection: The SQLAlchemy connection.

    Returns:
        The open psycopg connection.

    Raises:
        MigrationError: If the connection has been closed.

    Example:
        >>> driver_connection(connection).cursor()
    """
    driver = connection.connection.driver_connection
    if driver is None:
        raise MigrationError("the migration connection has been closed")
    return driver


def run_script(connection: Connection, module: str, script: MigrationScript) -> None:
    """Run one migration file's SQL as it is written, with no parameters.

    A no-transaction file is sent as a prepared statement, so PostgreSQL itself
    refuses it if it holds more than one statement.

    Args:
        connection: The migration connection.
        module: The module the file belongs to, for messages.
        script: The file to run.

    Raises:
        MigrationError: Naming the file and PostgreSQL's reason, if it fails.

    Example:
        >>> run_script(connection, "core", migration.upgrade)
    """
    try:
        with driver_connection(connection).cursor() as cursor:
            cursor.execute(script.sql, prepare=script.no_transaction)
    except psycopg.Error as error:
        reason = str(error).splitlines()[0] if str(error) else type(error).__name__
        if script.no_transaction and isinstance(error, postgres_errors.SyntaxError):
            reason = (
                "it runs outside a transaction, so it may hold only one statement "
                f"({reason})"
            )
        raise MigrationError(
            f"{module}: {script.file_name} failed: {reason}"
        ) from error


def _run_outside_transaction(
    connection: Connection, module: str, script: MigrationScript
) -> None:
    """Run a no-transaction file on its own, then return to normal transactions.

    Args:
        connection: The migration connection, with no open transaction.
        module: The module the file belongs to, for messages.
        script: The no-transaction file.

    Raises:
        MigrationError: As run_script does.

    Example:
        >>> _run_outside_transaction(connection, "core", migration.upgrade)
    """
    connection.execution_options(isolation_level="AUTOCOMMIT")
    try:
        run_script(connection, module, script)
    finally:
        connection.execution_options(isolation_level="READ COMMITTED")


def run_with_record(
    connection: Connection,
    module: str,
    script: MigrationScript,
    record: tuple[TextClause, dict[str, Any]],
) -> None:
    """Run a file and change its record, in one transaction unless it may not have one.

    Args:
        connection: The migration connection, with no open transaction.
        module: The module the file belongs to.
        script: The upgrade or down file to run.
        record: The statement that adds or removes the file's record, and its values.

    Raises:
        MigrationError: As run_script does. The record is then left as it was.

    Example:
        >>> run_with_record(connection, "core", m.upgrade, (ADD_RECORD, values))
    """
    statement, values = record
    if script.no_transaction:
        _run_outside_transaction(connection, module, script)
        with connection.begin():
            connection.execute(statement, values)
        return
    with connection.begin():
        run_script(connection, module, script)
        connection.execute(statement, values)


@contextmanager
def migration_lock(connection: Connection) -> Iterator[None]:
    """Hold the runner's advisory lock on this connection until the run ends.

    Args:
        connection: The migration connection, with no open transaction.

    Returns:
        A context manager that holds the lock while it is open.

    Raises:
        MigrationError: If the connection has an open transaction, or another
            run held the lock for longer than the migration statement timeout.

    Example:
        >>> with migration_lock(connection):
        ...     pass
    """
    if connection.in_transaction():
        raise MigrationError(
            "the migration runner needs a connection with no open transaction"
        )
    key = {"key": MIGRATION_LOCK_KEY}
    try:
        with connection.begin():
            connection.execute(text("SELECT pg_advisory_lock(:key)"), key)
    except DBAPIError as error:
        if isinstance(error.orig, postgres_errors.QueryCanceled):
            raise MigrationError(
                "another migration run held the lock for longer than the "
                "migration statement timeout"
            ) from error
        raise
    try:
        yield
    finally:
        with connection.begin():
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), key)
