"""Test fixtures for the engine: a fresh PostgreSQL 18 database for each test run.

The admin connection comes from BOWERSTONE_TEST_ADMIN_URL, a setting that only
the tests use. The test database is made the way docker/initdb/01-roles.sh
makes the development one: the built-in C.UTF-8 locale, owned by
bowerstone_owner, and closed to every role except bowerstone_app. Task 1.8
turns these fixtures into the testing kit for app repositories.
"""

import os
import uuid
from collections.abc import Callable, Iterator

import pytest
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from bowerstone_engine.config import (
    APP_ROLE,
    OWNER_ROLE,
    ApiSettings,
    ConnectionLimits,
    MigrationSettings,
)
from bowerstone_engine.db import create_connection_pool

LOCALE_PROVIDERS = frozenset({"builtin", "libc"})
TEST_LIMITS = ConnectionLimits(
    statement_timeout_ms=5_000,
    connect_timeout_seconds=5,
    pool_size=5,
    pool_timeout_seconds=5,
)

DatabaseFactory = Callable[[str], str]
PoolFactory = Callable[..., Engine]


def url_for_database(url: SecretStr, database: str) -> SecretStr:
    """Point a database address at another database on the same server.

    Args:
        url: The database address, kept secret.
        database: The name of the other database.

    Returns:
        The new address, kept secret.

    Example:
        >>> url_for_database(settings.database_url, "bowerstone_test_1a2b")
        SecretStr('**********')
    """
    moved = make_url(url.get_secret_value()).set(database=database)
    return SecretStr(moved.render_as_string(hide_password=False))


def create_database(admin_pool: Engine, name: str, locale_provider: str) -> None:
    """Create a database the way docker/initdb/01-roles.sh sets up the real one.

    Database names cannot be bound parameters, so psycopg's sql module quotes it.

    Args:
        admin_pool: Connections as the admin role, outside transactions.
        name: The new database's name.
        locale_provider: "builtin" for the right locale, or "libc" for a wrong one.

    Raises:
        ValueError: If the locale provider is not one of LOCALE_PROVIDERS.

    Example:
        >>> create_database(admin_pool, "bowerstone_test_1a2b", "builtin")
    """
    if locale_provider not in LOCALE_PROVIDERS:
        raise ValueError(f"unknown locale provider {locale_provider!r}")
    database = sql.Identifier(name)
    statements = [
        sql.SQL(
            "CREATE DATABASE {} OWNER {} TEMPLATE template0 ENCODING 'UTF8' "
            "LOCALE_PROVIDER {} LOCALE 'C.UTF-8'"
        ).format(database, sql.Identifier(OWNER_ROLE), sql.SQL(locale_provider)),
        sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(database),
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            database, sql.Identifier(APP_ROLE)
        ),
    ]
    with admin_pool.connect() as connection:
        for statement in statements:
            run_composed(connection, statement)


def run_composed(connection: Connection, statement: sql.Composed) -> None:
    """Run a statement whose names psycopg's sql module has quoted.

    Names of databases and roles cannot be bound parameters, so statements
    that need them are composed with sql.Identifier and run on the driver.

    Args:
        connection: An open connection.
        statement: The composed statement.

    Raises:
        RuntimeError: If the connection has already been closed.

    Example:
        >>> run_composed(connection, sql.SQL("DROP DATABASE {}").format(name))
    """
    driver = connection.connection.driver_connection
    if driver is None:
        raise RuntimeError("the database connection has already been closed")
    driver.execute(statement)


def drop_database(admin_pool: Engine, name: str) -> None:
    """Drop a test database, closing any connections still open to it.

    Args:
        admin_pool: Connections as the admin role, outside transactions.
        name: The database's name.

    Example:
        >>> drop_database(admin_pool, "bowerstone_test_1a2b")
    """
    statement = sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
    with admin_pool.connect() as connection:
        run_composed(connection, statement)


@pytest.fixture(scope="session")
def admin_pool() -> Iterator[Engine]:
    """Connections as the admin role, which run outside transactions."""
    url = os.environ.get("BOWERSTONE_TEST_ADMIN_URL")
    if not url:
        raise RuntimeError(
            "Set BOWERSTONE_TEST_ADMIN_URL to the admin role's database address. "
            "compose.dev.yaml sets it when the tests run through `make test`."
        )
    pool = create_engine(
        url,
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",
        hide_parameters=True,
        connect_args={"connect_timeout": TEST_LIMITS.connect_timeout_seconds},
    )
    yield pool
    pool.dispose()


@pytest.fixture(scope="session")
def test_database(admin_pool: Engine) -> Iterator[str]:
    """A fresh database for this test run, dropped when the run ends."""
    name = f"bowerstone_test_{uuid.uuid4().hex[:12]}"
    create_database(admin_pool, name, "builtin")
    yield name
    drop_database(admin_pool, name)


@pytest.fixture(scope="session")
def owner_url(test_database: str) -> SecretStr:
    """The owner role's address for the test database."""
    return url_for_database(MigrationSettings().owner_database_url, test_database)


@pytest.fixture(scope="session")
def app_url(test_database: str) -> SecretStr:
    """The app role's address for the test database."""
    return url_for_database(ApiSettings().database_url, test_database)


@pytest.fixture(scope="session")
def owner_url_for() -> Callable[[str], SecretStr]:
    """Give the owner role's address for any database on the test server."""
    owner_database_url = MigrationSettings().owner_database_url
    return lambda database: url_for_database(owner_database_url, database)


@pytest.fixture(scope="session")
def app_url_for() -> Callable[[str], SecretStr]:
    """Give the app role's address for any database on the test server."""
    app_database_url = ApiSettings().database_url
    return lambda database: url_for_database(app_database_url, database)


@pytest.fixture(scope="session")
def admin_url_for() -> Callable[[str], SecretStr]:
    """Give the admin role's address for any database on the test server."""
    admin_database_url = SecretStr(os.environ["BOWERSTONE_TEST_ADMIN_URL"])
    return lambda database: url_for_database(admin_database_url, database)


@pytest.fixture(scope="session")
def admin_url(test_database: str) -> SecretStr:
    """The admin role's address for the test database."""
    url = SecretStr(os.environ["BOWERSTONE_TEST_ADMIN_URL"])
    return url_for_database(url, test_database)


@pytest.fixture
def pool_factory() -> Iterator[PoolFactory]:
    """Make connection pools with chosen limits, and close them after the test."""
    pools: list[Engine] = []

    def make_pool(url: SecretStr, **limits: int) -> Engine:
        """Make a pool for the address, with TEST_LIMITS changed by limits."""
        checked_limits = ConnectionLimits(**{**TEST_LIMITS.model_dump(), **limits})
        pool = create_connection_pool(url, checked_limits)
        pools.append(pool)
        return pool

    yield make_pool
    for pool in pools:
        pool.dispose()


def _make_databases(admin_pool: Engine) -> Iterator[DatabaseFactory]:
    """Lend a maker of test databases, then drop every database it made.

    Args:
        admin_pool: Connections as the admin role, outside transactions.

    Returns:
        An iterator that yields the maker once, then drops its databases.

    Example:
        >>> yield from _make_databases(admin_pool)
    """
    names: list[str] = []

    def make_database(locale_provider: str) -> str:
        """Make a database with the given locale provider and return its name."""
        name = f"bowerstone_test_{uuid.uuid4().hex[:12]}"
        create_database(admin_pool, name, locale_provider)
        names.append(name)
        return name

    yield make_database
    for name in names:
        drop_database(admin_pool, name)


@pytest.fixture
def database_factory(admin_pool: Engine) -> Iterator[DatabaseFactory]:
    """Make extra test databases, and drop them after the test."""
    yield from _make_databases(admin_pool)


@pytest.fixture(scope="session")
def session_database_factory(admin_pool: Engine) -> Iterator[DatabaseFactory]:
    """Make test databases that last until the test run ends, then drop them."""
    yield from _make_databases(admin_pool)


def _connection_in_rolled_back_transaction(pool: Engine) -> Iterator[Connection]:
    """Lend a connection inside a transaction that is always rolled back.

    Args:
        pool: The pool to take the connection from.

    Returns:
        An iterator that yields the connection once, then rolls back.

    Example:
        >>> yield from _connection_in_rolled_back_transaction(owner_pool)
    """
    with pool.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            # A test may already have rolled back, to look at the state after.
            # Closing the connection then rolls back anything it began since.
            if transaction.is_active:
                transaction.rollback()


@pytest.fixture
def owner_connection(
    owner_url: SecretStr, pool_factory: PoolFactory
) -> Iterator[Connection]:
    """A connection to the test database as the owner role, rolled back after."""
    yield from _connection_in_rolled_back_transaction(pool_factory(owner_url))


@pytest.fixture
def app_connection(
    app_url: SecretStr, pool_factory: PoolFactory
) -> Iterator[Connection]:
    """A connection to the test database as the app role, rolled back after."""
    yield from _connection_in_rolled_back_transaction(pool_factory(app_url))


@pytest.fixture
def admin_transaction(admin_url: SecretStr) -> Iterator[Connection]:
    """A connection to the test database as the admin role, rolled back after.

    Role changes in PostgreSQL are transactional, so a test may rename or
    change a role here, and the rollback always puts it back.
    """
    pool = create_engine(
        admin_url.get_secret_value(),
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={"connect_timeout": TEST_LIMITS.connect_timeout_seconds},
    )
    try:
        yield from _connection_in_rolled_back_transaction(pool)
    finally:
        pool.dispose()
