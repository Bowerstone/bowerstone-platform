"""Fixtures for the core schema's tests (task 1.3).

Each test file gets a database of its own with the core migration applied, so
no file sees another's changes. Most tests then run in a transaction that is
rolled back, as the app role, the owner role, or the admin role.
"""

from collections.abc import Callable, Iterator
from importlib.resources import files
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from bowerstone_engine.config import ConnectionLimits
from bowerstone_engine.db import create_connection_pool
from bowerstone_engine.migration_runner.migrate import upgrade
from bowerstone_engine.migration_runner.migration_files import (
    Migration,
    read_migrations,
)

CORE_LIMITS = ConnectionLimits(
    statement_timeout_ms=10_000,
    connect_timeout_seconds=5,
    pool_size=3,
    pool_timeout_seconds=5,
)

UrlFor = Callable[[str], SecretStr]
Refusal = Callable[..., str]


def read_core_migrations() -> list[Migration]:
    """Read the core module's migrations from the engine package.

    Returns:
        The core migrations, from 0001.

    Example:
        >>> [m.name for m in read_core_migrations()]
        ['core']
    """
    return read_migrations("core", files("bowerstone_engine.core.migrations"))


def apply_core(owner_url: SecretStr) -> None:
    """Apply the core migrations to a database, as the owner role.

    Args:
        owner_url: The owner role's address for the database.

    Example:
        >>> apply_core(owner_url_for("bowerstone_test_1a2b"))
    """
    pool = create_connection_pool(owner_url, CORE_LIMITS)
    try:
        with pool.connect() as connection:
            upgrade(connection, "core", read_core_migrations())
    finally:
        pool.dispose()


def _rolled_back(pool: Engine) -> Iterator[Connection]:
    """Lend a connection inside a transaction that is always rolled back.

    Args:
        pool: The pool to take the connection from.

    Returns:
        An iterator that yields the connection once, then rolls back.

    Example:
        >>> yield from _rolled_back(app_pool)
    """
    with pool.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            if transaction.is_active:
                transaction.rollback()


@pytest.fixture
def core_migrations() -> list[Migration]:
    """The core module's migrations, read from the engine package."""
    return read_core_migrations()


@pytest.fixture(scope="module")
def core_database(
    session_database_factory: Callable[[str], str], owner_url_for: UrlFor
) -> str:
    """A database for this test file, with the core migration applied."""
    name = session_database_factory("builtin")
    apply_core(owner_url_for(name))
    return name


@pytest.fixture(scope="module")
def core_pools(
    core_database: str,
    owner_url_for: UrlFor,
    app_url_for: UrlFor,
    admin_url_for: UrlFor,
) -> Iterator[dict[str, Engine]]:
    """Connections to this file's database as the app, owner, and admin roles."""
    pools = {
        "app": create_connection_pool(app_url_for(core_database), CORE_LIMITS),
        "owner": create_connection_pool(owner_url_for(core_database), CORE_LIMITS),
        "admin": create_connection_pool(admin_url_for(core_database), CORE_LIMITS),
    }
    yield pools
    for pool in pools.values():
        pool.dispose()


@pytest.fixture
def as_app(core_pools: dict[str, Engine]) -> Iterator[Connection]:
    """A connection as bowerstone_app, in a transaction rolled back after the test."""
    yield from _rolled_back(core_pools["app"])


@pytest.fixture
def as_owner(core_pools: dict[str, Engine]) -> Iterator[Connection]:
    """A connection as bowerstone_owner, in a transaction rolled back after the test."""
    yield from _rolled_back(core_pools["owner"])


@pytest.fixture
def as_admin(core_pools: dict[str, Engine]) -> Iterator[Connection]:
    """A connection as the admin role, in a transaction rolled back after the test."""
    yield from _rolled_back(core_pools["admin"])


@pytest.fixture
def refusal() -> Refusal:
    """Run a statement that must fail, and give PostgreSQL's error code for it."""

    def attempt(connection: Connection, statement: str, **values: Any) -> str:
        """Run the statement inside a savepoint, and return its SQLSTATE.

        The savepoint is rolled back either way, so the test's transaction stays
        usable for further attempts.
        """
        savepoint = connection.begin_nested()
        try:
            connection.execute(text(statement), values)
        except DBAPIError as error:
            savepoint.rollback()
            return str(getattr(error.orig, "sqlstate", ""))
        savepoint.rollback()
        raise AssertionError(
            f"PostgreSQL accepted a statement it should refuse: {statement}"
        )

    return attempt
