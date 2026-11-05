"""Connections to Postgres, and setting up a new database.

Every engine has a fixed-size pool, and every connection it opens has a
statement timeout. `set_current_user` names the user for the change history.
"""

import psycopg
import sqlalchemy
from psycopg import sql
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
    """Connections for migrations, as the owner role.

    They get the migration timeout, which is longer than a request's, because
    a migration may rewrite a large table.
    """
    return _engine_from(
        settings,
        settings.owner_database_url,
        "BOWERSTONE_OWNER_DATABASE_URL",
        statement_timeout_ms=settings.migration_timeout_ms,
    )


def setup_database(settings: Settings, admin_url: str | None = None) -> list[str]:
    """Creates Bowerstone's two roles and its database, through an admin's connection.

    Used once, on a new desktop install. The roles' passwords and the
    database's name come from BOWERSTONE_OWNER_DATABASE_URL and
    BOWERSTONE_DATABASE_URL. The admin's address is `admin_url`, or else
    BOWERSTONE_ADMIN_DATABASE_URL. A role or database that already exists is
    left as it is, so running this again is safe. Returns what it did, one
    sentence per step.
    """
    admin = admin_url or _secret(settings.admin_database_url)
    if not admin:
        raise SettingsError(
            "Give the admin's address with --admin-url, "
            "or set BOWERSTONE_ADMIN_DATABASE_URL."
        )
    owner = _role_url(
        settings.owner_database_url, OWNER_ROLE, "BOWERSTONE_OWNER_DATABASE_URL"
    )
    app = _role_url(settings.database_url, APP_ROLE, "BOWERSTONE_DATABASE_URL")
    if owner.database != app.database:
        raise SettingsError(
            "BOWERSTONE_OWNER_DATABASE_URL and BOWERSTONE_DATABASE_URL "
            "must name the same database."
        )

    steps = []
    database = sql.Identifier(str(owner.database))
    dsn = (
        postgres_url(admin)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    with psycopg.connect(dsn, autocommit=True) as pg:
        for role, url in ((OWNER_ROLE, owner), (APP_ROLE, app)):
            if pg.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
            ).fetchone():
                steps.append(
                    f"The role {role} already exists, so it was left as it is."
                )
                continue
            pg.execute(
                sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(url.password)
                )
            )
            steps.append(f"Created the role {role}.")

        if pg.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (owner.database,)
        ).fetchone():
            steps.append(
                f"The database {owner.database} already exists, "
                "so it was left as it is."
            )
        else:
            pg.execute(
                sql.SQL(
                    "CREATE DATABASE {} OWNER {} TEMPLATE template0 ENCODING 'UTF8' "
                    "LOCALE_PROVIDER builtin BUILTIN_LOCALE 'C.UTF-8' LOCALE 'C.UTF-8'"
                ).format(database, sql.Identifier(OWNER_ROLE))
            )
            steps.append(f"Created the database {owner.database}.")

        # Only Bowerstone's two roles may connect, as the roles script also sets up.
        pg.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(database))
        pg.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                database, sql.Identifier(APP_ROLE)
            )
        )
    return steps


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


def _engine_from(
    settings: Settings,
    url: SecretStr | None,
    variable: str,
    *,
    statement_timeout_ms: int | None = None,
) -> Engine:
    if url is None:
        raise SettingsError(f"{variable} is not set.")
    return create_engine(
        url.get_secret_value(),
        pool_size=settings.pool_size,
        pool_timeout_s=settings.pool_timeout_s,
        statement_timeout_ms=statement_timeout_ms or settings.statement_timeout_ms,
    )


def _secret(value: SecretStr | None) -> str | None:
    return None if value is None else value.get_secret_value()


def _role_url(value: SecretStr | None, role: str, variable: str) -> URL:
    if value is None:
        raise SettingsError(f"{variable} is not set.")
    url = postgres_url(value.get_secret_value())
    if url.username != role:
        raise SettingsError(f"{variable} must sign in as {role}.")
    if not url.password:
        raise SettingsError(f"{variable} must include {role}'s password.")
    return url
