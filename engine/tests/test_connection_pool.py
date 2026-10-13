"""Tests for bowerstone_engine.db.create_connection_pool: fixed size and timeouts."""

import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack

import pytest
from psycopg import errors as postgres_errors
from pydantic import SecretStr
from sqlalchemy import Engine, exc, text

from bowerstone_engine.config import APP_ROLE

PoolFactory = Callable[..., Engine]


@pytest.fixture
def silent_server() -> Iterator[int]:
    """A local port that accepts connections but never answers, like a hung server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        yield listener.getsockname()[1]


def test_every_pooled_connection_starts_with_the_limits(
    app_url: SecretStr, pool_factory: PoolFactory
) -> None:
    """Each connection the pool opens has the statement timeout and UTC time zone."""
    pool = pool_factory(app_url, pool_size=3, statement_timeout_ms=1234)
    with ExitStack() as held:
        connections = [held.enter_context(pool.connect()) for _ in range(3)]
        settings = [
            tuple(
                connection.execute(
                    text(
                        "SELECT current_setting('statement_timeout'), "
                        "current_setting('TimeZone'), pg_backend_pid()"
                    )
                ).one()
            )
            for connection in connections
        ]
    assert {setting[:2] for setting in settings} == {("1234ms", "UTC")}
    assert len({setting[2] for setting in settings}) == 3


def test_the_statement_timeout_stops_a_slow_query(
    app_url: SecretStr, pool_factory: PoolFactory
) -> None:
    """A query that runs past the statement timeout is cancelled by PostgreSQL."""
    pool = pool_factory(app_url, statement_timeout_ms=100)
    started = time.monotonic()
    with pool.connect() as connection, pytest.raises(exc.OperationalError) as stopped:
        connection.execute(text("SELECT pg_sleep(1)"))
    assert isinstance(stopped.value.orig, postgres_errors.QueryCanceled)
    assert time.monotonic() - started < 0.9


def test_a_query_within_the_timeout_finishes(
    app_url: SecretStr, pool_factory: PoolFactory
) -> None:
    """A query that ends before the timeout is not disturbed."""
    pool = pool_factory(app_url, statement_timeout_ms=2_000)
    with pool.connect() as connection:
        assert connection.execute(text("SELECT pg_sleep(0.05), 1")).one()[1] == 1


def test_the_pool_never_opens_more_than_its_size(
    app_url: SecretStr,
    test_database: str,
    pool_factory: PoolFactory,
    admin_pool: Engine,
) -> None:
    """With every connection in use, the next request waits, then fails clearly."""
    pool = pool_factory(app_url, pool_size=2, pool_timeout_seconds=1)
    with pool.connect(), pool.connect():
        started = time.monotonic()
        with pytest.raises(exc.TimeoutError):
            pool.connect()
        waited = time.monotonic() - started
        with admin_pool.connect() as admin:
            open_on_server = admin.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = :database AND usename = :role"
                ),
                {"database": test_database, "role": APP_ROLE},
            ).scalar_one()
    assert 0.9 <= waited < 3
    assert open_on_server == 2


def test_a_waiting_request_gets_the_next_free_connection(
    app_url: SecretStr, pool_factory: PoolFactory
) -> None:
    """A request queued behind a busy pool proceeds as soon as a connection returns."""
    pool = pool_factory(app_url, pool_size=1, pool_timeout_seconds=5)
    results: list[int] = []

    def wait_for_a_connection() -> None:
        """Take a connection once one is free, and run a query on it."""
        with pool.connect() as connection:
            results.append(connection.execute(text("SELECT 7")).scalar_one())

    with pool.connect():
        waiter = threading.Thread(target=wait_for_a_connection)
        waiter.start()
        time.sleep(0.3)
        assert results == []
    waiter.join(timeout=5)
    assert results == [7]


def test_the_connect_timeout_stops_a_server_that_never_answers(
    silent_server: int, pool_factory: PoolFactory
) -> None:
    """Opening a connection to a hung server fails after the connect timeout."""
    url = SecretStr(
        f"postgresql+psycopg://{APP_ROLE}:made-up-password@127.0.0.1:{silent_server}/none"
    )
    pool = pool_factory(url, connect_timeout_seconds=2)
    started = time.monotonic()
    with pytest.raises(exc.OperationalError):
        pool.connect()
    assert 1.5 <= time.monotonic() - started < 5


def test_sqlalchemy_leaves_bound_values_out_of_its_error_text(
    app_url: SecretStr, pool_factory: PoolFactory
) -> None:
    """SQLAlchemy's part of an error names the query but not the values bound to it."""
    pool = pool_factory(app_url)
    marker = "value-that-must-not-appear"
    with pool.connect() as connection, pytest.raises(exc.DataError) as failed:
        connection.execute(
            text("SELECT CAST(:value AS text), 1 / 0"), {"value": marker}
        )
    assert "[SQL parameters hidden due to hide_parameters=True]" in str(failed.value)
    assert marker not in str(failed.value)


def test_postgresql_messages_can_still_quote_a_value(
    app_url: SecretStr, pool_factory: PoolFactory
) -> None:
    """PostgreSQL's own message can quote a bound value, so task 1.5 must hide it.

    This records a limit of the pool, not a goal: the API's error handling must
    never show or log a database message as it is.
    """
    pool = pool_factory(app_url)
    marker = "value-quoted-by-postgresql"
    with pool.connect() as connection, pytest.raises(exc.DataError) as failed:
        connection.execute(text("SELECT CAST(:value AS integer)"), {"value": marker})
    assert marker in str(failed.value.orig)
    assert f"parameter $1 = '{marker}'" not in str(failed.value.orig)
