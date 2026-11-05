"""Postgres servers and throwaway databases for tests.

`PostgresServer.prepare` takes an admin address, such as the one in
BOWERSTONE_TEST_ADMIN_URL, and makes sure the server has Bowerstone's two
roles, with new passwords for this test run. `scratch_database` then creates a
fresh database owned by the owner role, with the built-in C.UTF-8 locale, and
drops it afterwards.

Use test servers only: preparing a server changes the two roles' passwords.
"""

import secrets
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field

import sqlalchemy
from psycopg import sql
from sqlalchemy import Connection, Engine, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from bowerstone_engine import db
from bowerstone_engine.config import postgres_url

ADMIN_URL_VARIABLE = "BOWERSTONE_TEST_ADMIN_URL"

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
            db.OWNER_ROLE: secrets.token_urlsafe(24),
            db.APP_ROLE: secrets.token_urlsafe(24),
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
        database = sql.Identifier(name)
        with _admin_connection(self.admin_url) as conn:
            _run(
                conn,
                sql.SQL(
                    "CREATE DATABASE {} OWNER {} TEMPLATE template0 ENCODING 'UTF8' "
                    + locale
                ).format(database, sql.Identifier(db.OWNER_ROLE)),
            )
        try:
            with _admin_connection(self.admin_url) as conn:
                _run(
                    conn,
                    sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(database),
                )
                _run(
                    conn,
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                        database, sql.Identifier(db.APP_ROLE)
                    ),
                )
            yield ScratchDatabase(self, name)
        finally:
            with _admin_connection(self.admin_url) as conn:
                _run(
                    conn,
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(database),
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
        return db.create_engine(
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
