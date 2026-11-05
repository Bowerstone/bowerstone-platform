"""Connections to Postgres.

A fixed-size pool, a statement timeout on every connection, and the user for
the change history.
"""

import time

import psycopg
import pytest
import sqlalchemy.exc
from sqlalchemy import Connection, text

from bowerstone_engine.config import SettingsError, load_settings
from bowerstone_engine.db import (
    APP_ROLE,
    OWNER_ROLE,
    app_engine,
    create_engine,
    owner_engine,
    set_current_user,
)
from bowerstone_engine.testing import ScratchDatabase


def test_every_connection_starts_with_the_statement_timeout(
    database: ScratchDatabase,
) -> None:
    engine = database.engine(APP_ROLE, pool_size=2, statement_timeout_ms=250)
    try:
        with engine.connect() as first, engine.connect() as second:
            for conn in (first, second):
                assert (
                    conn.execute(text("SHOW statement_timeout")).scalar_one() == "250ms"
                )
    finally:
        engine.dispose()


def test_the_statement_timeout_stops_a_slow_query(database: ScratchDatabase) -> None:
    engine = database.engine(APP_ROLE, statement_timeout_ms=200)
    try:
        with engine.connect() as conn:
            started = time.monotonic()
            with pytest.raises(sqlalchemy.exc.OperationalError) as error:
                conn.execute(text("SELECT pg_sleep(10)"))
            assert isinstance(error.value.orig, psycopg.errors.QueryCanceled)
            assert time.monotonic() - started < 5
            conn.rollback()
            assert conn.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        engine.dispose()


def test_the_pool_never_grows_past_its_size(database: ScratchDatabase) -> None:
    engine = database.engine(APP_ROLE, pool_size=2, pool_timeout_s=0.2)
    try:
        with (
            engine.connect(),
            engine.connect(),
            pytest.raises(sqlalchemy.exc.TimeoutError),
        ):
            engine.connect()
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("pool_size", "statement_timeout_ms"),
    [pytest.param(0, 1000, id="pool of 0"), pytest.param(1, 0, id="timeout of 0")],
)
def test_create_engine_refuses_settings_that_turn_a_limit_off(
    pool_size: int, statement_timeout_ms: int
) -> None:
    with pytest.raises(ValueError, match="must be at least 1"):
        create_engine(
            "postgresql://user@localhost/db",
            pool_size=pool_size,
            pool_timeout_s=1,
            statement_timeout_ms=statement_timeout_ms,
        )


def user_setting(conn: Connection) -> str | None:
    value: str | None = conn.execute(
        text("SELECT current_setting('bowerstone.user_id', true)")
    ).scalar_one()
    return value


def test_set_current_user_lasts_until_the_transaction_ends(
    database: ScratchDatabase,
) -> None:
    with database.connect(APP_ROLE) as conn:
        set_current_user(conn, 42)
        assert user_setting(conn) == "42"
        conn.commit()
        assert user_setting(conn) in ("", None)

        set_current_user(conn, 7)
        conn.rollback()
        assert user_setting(conn) in ("", None)


def test_set_current_user_with_none_records_no_user(database: ScratchDatabase) -> None:
    with database.connect(APP_ROLE) as conn:
        set_current_user(conn, None)
        assert user_setting(conn) == ""


def test_app_engine_uses_the_settings(
    database: ScratchDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "BOWERSTONE_DATABASE_URL",
        database.url(APP_ROLE).render_as_string(hide_password=False),
    )
    monkeypatch.setenv("BOWERSTONE_STATEMENT_TIMEOUT_MS", "1234")
    monkeypatch.setenv("BOWERSTONE_POOL_SIZE", "3")
    engine = app_engine(load_settings())
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT current_user")).scalar_one() == APP_ROLE
            assert conn.execute(text("SHOW statement_timeout")).scalar_one() == "1234ms"
        assert engine.pool.size() == 3  # type: ignore[attr-defined]
    finally:
        engine.dispose()


def test_owner_engine_uses_the_owner_address(
    database: ScratchDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_url = database.url(OWNER_ROLE).render_as_string(hide_password=False)
    monkeypatch.setenv("BOWERSTONE_OWNER_DATABASE_URL", owner_url)
    monkeypatch.delenv("BOWERSTONE_MIGRATION_TIMEOUT_MS", raising=False)
    engine = owner_engine(load_settings())
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT current_user")).scalar_one() == OWNER_ROLE
            # Migrations get the longer migration timeout, an hour by default.
            assert conn.execute(text("SHOW statement_timeout")).scalar_one() == "1h"
    finally:
        engine.dispose()


def test_an_engine_needs_its_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOWERSTONE_DATABASE_URL", raising=False)
    monkeypatch.delenv("BOWERSTONE_OWNER_DATABASE_URL", raising=False)
    settings = load_settings()
    with pytest.raises(SettingsError, match=r"^BOWERSTONE_DATABASE_URL is not set\.$"):
        app_engine(settings)
    with pytest.raises(
        SettingsError, match=r"^BOWERSTONE_OWNER_DATABASE_URL is not set\.$"
    ):
        owner_engine(settings)
