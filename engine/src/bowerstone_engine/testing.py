"""The testing kit: a pytest plugin that every product repository uses.

Installing the engine registers the plugin, through the `pytest11` entry
point, so a product's tests get its fixtures with no import. The product names
itself once, in its pytest settings:

    [tool.pytest.ini_options]
    bowerstone_product = "accounting"

The tests need a Postgres 18 server for tests, whose admin address is in
BOWERSTONE_TEST_ADMIN_URL. Use a test server only: the kit gives the two roles
new passwords for each test run.

The fixtures:

- `product_database`: one fresh database for the whole test run, with the
  built-in C.UTF-8 locale, the two roles, the core's migrations, and the
  product under test installed, with the products it needs.
- `db`: a connection to it as bowerstone_app, whose transaction is rolled back
  after each test. Do not commit on it.
- `api`: an API client for the installed products' routes. Each request runs
  in a savepoint on `db`, so the test sees what requests write, and all of it
  is rolled back with `db`.
- `committed_database`: a separate database of the test's own, set up the same
  way, where transactions commit, for race tests with several connections. It
  is dropped after the test.

`PostgresServer` and `ScratchDatabase` are the pieces underneath, for tests
that need more.
"""

import os
import secrets
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
import sqlalchemy
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import Connection, Engine, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from bowerstone_engine import db as engine_db
from bowerstone_engine import deps, installer
from bowerstone_engine.config import Settings, postgres_url
from bowerstone_engine.products import ProductManifest, load_products

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

ADMIN_URL_VARIABLE = "BOWERSTONE_TEST_ADMIN_URL"
PRODUCT_SETTING = "bowerstone_product"

BUILTIN_C_UTF8 = "LOCALE_PROVIDER builtin BUILTIN_LOCALE 'C.UTF-8' LOCALE 'C.UTF-8'"
"""The locale clause of CREATE DATABASE that Bowerstone needs."""

MakePackage = Callable[[str, Mapping[str, str]], str]
"""Makes an importable package holding the given files, and returns its name."""


@dataclass(frozen=True)
class PostgresServer:
    """A test server with the owner and app roles, and their passwords for this run."""

    admin_url: URL
    # Left out of repr, so a failing test never prints them.
    passwords: Mapping[str, str] = field(repr=False)

    @classmethod
    def prepare(cls, admin_url: str) -> "PostgresServer":
        """Creates the two roles, or gives them new passwords, at `admin_url`."""
        url = postgres_url(admin_url)
        passwords = {
            engine_db.OWNER_ROLE: secrets.token_urlsafe(24),
            engine_db.APP_ROLE: secrets.token_urlsafe(24),
        }
        with _admin_connection(url) as conn:
            for role, password in passwords.items():
                exists = conn.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}
                ).first()
                statement = (
                    "ALTER ROLE {} WITH LOGIN NOSUPERUSER PASSWORD {}"
                    if exists
                    else "CREATE ROLE {} WITH LOGIN PASSWORD {}"
                )
                _run(
                    conn,
                    sql.SQL(statement).format(
                        sql.Identifier(role), sql.Literal(password)
                    ),
                )
        return cls(url, passwords)

    @property
    def admin_name(self) -> str:
        return str(self.admin_url.username)

    def url(self, role: str, database: str) -> URL:
        """The address of `database`, as either Bowerstone role or the admin."""
        if role == self.admin_name:
            return self.admin_url.set(database=database)
        return self.admin_url.set(
            username=role, password=self.passwords[role], database=database
        )

    @contextmanager
    def scratch_database(
        self, locale: str = BUILTIN_C_UTF8
    ) -> Iterator["ScratchDatabase"]:
        """A fresh database, as the roles script sets one up, dropped afterwards.

        `locale` is the locale clause of CREATE DATABASE. Tests pass another one
        to get a database with the wrong locale.
        """
        name = f"bw_test_{secrets.token_hex(6)}"
        identifier = sql.Identifier(name)
        with _admin_connection(self.admin_url) as conn:
            _run(
                conn,
                sql.SQL(
                    "CREATE DATABASE {} OWNER {} TEMPLATE template0 ENCODING 'UTF8' "
                    + locale
                ).format(identifier, sql.Identifier(engine_db.OWNER_ROLE)),
            )
        try:
            with _admin_connection(self.admin_url) as conn:
                _run(
                    conn,
                    sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(identifier),
                )
                _run(
                    conn,
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                        identifier, sql.Identifier(engine_db.APP_ROLE)
                    ),
                )
            yield ScratchDatabase(self, name)
        finally:
            with _admin_connection(self.admin_url) as conn:
                _run(
                    conn,
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        identifier
                    ),
                )


@dataclass(frozen=True)
class ScratchDatabase:
    """A throwaway database on a test server."""

    server: PostgresServer
    name: str

    def url(self, role: str) -> URL:
        return self.server.url(role, self.name)

    def engine(
        self,
        role: str,
        *,
        pool_size: int = 2,
        pool_timeout_s: float = 5.0,
        statement_timeout_ms: int = 30_000,
    ) -> Engine:
        """An engine made the way the engine makes its own, signed in as `role`."""
        return engine_db.create_engine(
            self.url(role),
            pool_size=pool_size,
            pool_timeout_s=pool_timeout_s,
            statement_timeout_ms=statement_timeout_ms,
        )

    @contextmanager
    def connect(self, role: str) -> Iterator[Connection]:
        """One connection as `role`. Its open transaction is rolled back at the end."""
        engine = self.engine(role)
        try:
            with engine.connect() as conn:
                yield conn
        finally:
            engine.dispose()

    def install(
        self, key: str, products: Mapping[str, ProductManifest] | None = None
    ) -> list[str]:
        """Installs the product `key` and the products it needs, as the owner role.

        The core's migrations run first. Returns the keys of the products installed.
        """
        engine = self.engine(engine_db.OWNER_ROLE)
        try:
            return installer.install(engine, key, products)
        finally:
            engine.dispose()

    @contextmanager
    def connect_as_admin(self) -> Iterator[Connection]:
        """One connection as the admin. Its open transaction is rolled back."""
        engine = sqlalchemy.create_engine(
            self.url(self.server.admin_name), poolclass=NullPool
        )
        try:
            with engine.connect() as conn:
                yield conn
        finally:
            engine.dispose()


@contextmanager
def _admin_connection(url: URL) -> Iterator[Connection]:
    # Outside a transaction, because CREATE and DROP DATABASE cannot run inside one.
    engine = sqlalchemy.create_engine(
        url, poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        with engine.connect() as conn:
            yield conn
    finally:
        engine.dispose()


def _run(conn: Connection, statement: sql.Composed) -> None:
    conn.exec_driver_sql(statement.as_string())


# The pytest plugin.


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addini(
        PRODUCT_SETTING, "The key of the product under test, such as accounting."
    )


@pytest.fixture(scope="session")
def bowerstone_server() -> PostgresServer:
    """The Postgres 18 test server, with the two roles prepared for this run."""
    url = os.environ.get(ADMIN_URL_VARIABLE)
    if not url:
        pytest.fail(
            f"{ADMIN_URL_VARIABLE} is not set. It must hold the admin address of a "
            "Postgres 18 server for tests.",
            pytrace=False,
        )
    return PostgresServer.prepare(url)


@pytest.fixture(scope="session")
def bowerstone_products() -> dict[str, ProductManifest]:
    """Every product present, by key."""
    return load_products()


@pytest.fixture(scope="session")
def bowerstone_product(
    request: pytest.FixtureRequest, bowerstone_products: dict[str, ProductManifest]
) -> ProductManifest:
    """The manifest of the product under test."""
    key = request.config.getini(PRODUCT_SETTING)
    if not key:
        pytest.fail(
            f"Set {PRODUCT_SETTING} in [tool.pytest.ini_options] to the key of the "
            "product under test.",
            pytrace=False,
        )
    if key not in bowerstone_products:
        pytest.fail(
            f"The product {key}'s package is not installed, so it cannot be tested.",
            pytrace=False,
        )
    return bowerstone_products[key]


def _installed_database(
    server: PostgresServer,
    product: ProductManifest,
    products: Mapping[str, ProductManifest],
) -> Iterator[ScratchDatabase]:
    with server.scratch_database() as scratch:
        scratch.install(product.key, products)
        yield scratch


@pytest.fixture(scope="session")
def product_database(
    bowerstone_server: PostgresServer,
    bowerstone_product: ProductManifest,
    bowerstone_products: dict[str, ProductManifest],
) -> Iterator[ScratchDatabase]:
    """One database for the test run, with the product under test installed."""
    yield from _installed_database(
        bowerstone_server, bowerstone_product, bowerstone_products
    )


@pytest.fixture(scope="session")
def bowerstone_app_engine(product_database: ScratchDatabase) -> Iterator[Engine]:
    """The engine `db` takes its connections from, as bowerstone_app."""
    engine = product_database.engine(engine_db.APP_ROLE)
    yield engine
    engine.dispose()


@pytest.fixture
def db(bowerstone_app_engine: Engine) -> Iterator[Connection]:
    """A connection as bowerstone_app, rolled back after the test. Do not commit."""
    with bowerstone_app_engine.connect() as conn:
        yield conn
        conn.rollback()


@pytest.fixture
def committed_database(
    bowerstone_server: PostgresServer,
    bowerstone_product: ProductManifest,
    bowerstone_products: dict[str, ProductManifest],
) -> Iterator[ScratchDatabase]:
    """A database of the test's own, where transactions commit, dropped afterwards."""
    yield from _installed_database(
        bowerstone_server, bowerstone_product, bowerstone_products
    )


@pytest.fixture(scope="session")
def bowerstone_app(
    product_database: ScratchDatabase,
    bowerstone_products: dict[str, ProductManifest],
) -> Iterator["FastAPI"]:
    """The API, as the engine builds it, on `product_database`."""
    from bowerstone_engine.main import create_app

    url = product_database.url(engine_db.APP_ROLE).render_as_string(hide_password=False)
    app = create_app(
        Settings(env="development", database_url=SecretStr(url)), bowerstone_products
    )
    yield app
    app.state.engine.dispose()


@pytest.fixture
def api(bowerstone_app: "FastAPI", db: Connection) -> Iterator["TestClient"]:
    """An API client. Its requests run in savepoints on `db`, rolled back with it."""
    from fastapi.testclient import TestClient

    def shared_connection(user: deps.CurrentUser) -> Iterator[Connection]:
        # Like the engine's own: one transaction per request, here a savepoint,
        # released when the route succeeds and rolled back when it fails.
        with db.begin_nested():
            engine_db.set_current_user(db, user.id)
            yield db

    bowerstone_app.dependency_overrides[deps.database] = shared_connection
    client = TestClient(bowerstone_app)
    try:
        yield client
    finally:
        client.close()
        bowerstone_app.dependency_overrides.pop(deps.database, None)
