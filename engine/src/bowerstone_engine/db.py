"""Connections to Postgres.

Every engine has a fixed-size pool, and every connection it opens has a
statement timeout. `set_current_user` names the user for the change history.
"""

import sqlalchemy
from pydantic import SecretStr
from sqlalchemy import Connection, Engine, text
from sqlalchemy.engine import URL

from bowerstone_engine.config import Settings, SettingsError, postgres_url

OWNER_ROLE = "bowerstone_owner"
"""Owns the tables and runs migrations. It is not a superuser."""

APP_ROLE = "bowerstone_app"
"""The running engine's role. It owns nothing."""

APPLICATION_NAME = "bowerstone-engine"


def create_engine(
    url: str | URL,
    *,
    pool_size: int,
    pool_timeout_s: float,
    statement_timeout_ms: int,
) -> Engine:
    """Creates an engine for a Postgres address.

    The pool never grows past `pool_size`. A checkout that waits longer than
    `pool_timeout_s` fails with sqlalchemy.exc.TimeoutError. Every connection
    starts with `statement_timeout` set, so a slow query is stopped.
    """
    if pool_size < 1:
        # SQLAlchemy reads 0 as "no limit".
        raise ValueError("pool_size must be at least 1")
    if statement_timeout_ms < 1:
        raise ValueError(
            "statement_timeout_ms must be at least 1, because 0 turns the timeout off"
        )
    return sqlalchemy.create_engine(
        postgres_url(url),
        pool_size=pool_size,
        max_overflow=0,
        pool_timeout=pool_timeout_s,
        pool_pre_ping=True,
        connect_args={
            # Set when the connection starts, so it holds for every statement on it.
            "options": f"-c statement_timeout={statement_timeout_ms}",
            "application_name": APPLICATION_NAME,
        },
    )


def app_engine(settings: Settings) -> Engine:
    """The running engine's connections, as the app role."""
    return _engine_from(settings, settings.database_url, "BOWERSTONE_DATABASE_URL")


def owner_engine(settings: Settings) -> Engine:
    """Connections for migrations, as the owner role."""
    return _engine_from(
        settings, settings.owner_database_url, "BOWERSTONE_OWNER_DATABASE_URL"
    )


def set_current_user(conn: Connection, user_id: int | None) -> None:
    """Names the user that the change history records, until the transaction ends.

    None records no user. The setting is local to the current transaction, so a
    pooled connection never carries one request's user into the next.
    """
    value = "" if user_id is None else str(user_id)
    conn.execute(
        text("SELECT set_config('bowerstone.user_id', :user_id, true)"),
        {"user_id": value},
    )


def _engine_from(settings: Settings, url: SecretStr | None, variable: str) -> Engine:
    if url is None:
        raise SettingsError(f"{variable} is not set.")
    return create_engine(
        url.get_secret_value(),
        pool_size=settings.pool_size,
        pool_timeout_s=settings.pool_timeout_s,
        statement_timeout_ms=settings.statement_timeout_ms,
    )
