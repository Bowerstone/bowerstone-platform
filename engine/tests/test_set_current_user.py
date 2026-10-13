"""Tests for bowerstone_engine.db.set_current_user: who is making a change."""

import threading
from collections.abc import Callable
from typing import Any, cast

import pytest
from pydantic import SecretStr
from sqlalchemy import Connection, Engine, text

from bowerstone_engine.db import LARGEST_USER_ID, set_current_user

PoolFactory = Callable[..., Engine]

# The value the change history records: NULL when no user is set.
RECORDED_USER = text("SELECT nullif(current_setting('bowerstone.user_id', true), '')")


def recorded_user(connection: Connection) -> str | None:
    """Read the user the change history would record on this connection.

    Args:
        connection: The connection to read from.

    Returns:
        The user ID as text, or None when no user is recorded.

    Example:
        >>> recorded_user(connection)
        '42'
    """
    return cast(str | None, connection.execute(RECORDED_USER).scalar_one())


def test_the_user_is_recorded_for_the_transaction(
    app_url: SecretStr, pool_factory: PoolFactory
) -> None:
    """Inside the transaction, the change history sees the user."""
    with pool_factory(app_url).begin() as connection:
        set_current_user(connection, 42)
        assert recorded_user(connection) == "42"


def test_no_user_records_nothing(app_url: SecretStr, pool_factory: PoolFactory) -> None:
    """None, the development user before sign-in exists, records no user."""
    with pool_factory(app_url).begin() as connection:
        set_current_user(connection, None)
        assert recorded_user(connection) is None


def test_the_largest_user_id_is_accepted(
    app_url: SecretStr, pool_factory: PoolFactory
) -> None:
    """The largest bigint is a valid user ID."""
    with pool_factory(app_url).begin() as connection:
        set_current_user(connection, LARGEST_USER_ID)
        assert recorded_user(connection) == str(LARGEST_USER_ID)


@pytest.mark.parametrize("finish", ["commit", "rollback"])
def test_the_user_is_forgotten_when_the_transaction_ends(
    app_url: SecretStr, pool_factory: PoolFactory, finish: str
) -> None:
    """After a commit or a rollback, the same connection records no user."""
    with pool_factory(app_url).connect() as connection:
        connection.begin()
        set_current_user(connection, 42)
        getattr(connection, finish)()
        with connection.begin():
            assert recorded_user(connection) is None


def test_a_pooled_connection_does_not_pass_on_the_user(
    app_url: SecretStr, pool_factory: PoolFactory
) -> None:
    """The next request on the same pooled connection starts with no user."""
    pool = pool_factory(app_url, pool_size=1)
    with pool.begin() as first:
        set_current_user(first, 7)
        first_backend = first.execute(text("SELECT pg_backend_pid()")).scalar_one()
    with pool.begin() as second:
        second_backend = second.execute(text("SELECT pg_backend_pid()")).scalar_one()
        assert recorded_user(second) is None
    assert first_backend == second_backend


def test_concurrent_transactions_each_see_only_their_own_user(
    app_url: SecretStr, pool_factory: PoolFactory
) -> None:
    """Two requests at the same time never see each other's user."""
    pool = pool_factory(app_url, pool_size=2)
    both_set = threading.Barrier(2, timeout=5)
    seen: dict[int, str | None] = {}

    def act_as(user_id: int) -> None:
        """Record a user, wait until the other thread has too, then read it back."""
        with pool.begin() as connection:
            set_current_user(connection, user_id)
            both_set.wait()
            seen[user_id] = recorded_user(connection)

    threads = [threading.Thread(target=act_as, args=(user_id,)) for user_id in (11, 22)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert seen == {11: "11", 22: "22"}


def test_outside_a_transaction_the_user_is_refused(
    app_url: SecretStr, pool_factory: PoolFactory
) -> None:
    """Without an open transaction the value would vanish, so the call fails loudly."""
    with pool_factory(app_url).connect() as connection:
        with pytest.raises(RuntimeError, match="needs an open transaction"):
            set_current_user(connection, 1)
        assert not connection.in_transaction()


@pytest.mark.parametrize(
    "user_id", [0, -1, True, False, LARGEST_USER_ID + 1, 1.5, "5", "", b"5"]
)
def test_an_invalid_user_id_is_refused(
    app_url: SecretStr, pool_factory: PoolFactory, user_id: object
) -> None:
    """Only whole numbers from 1 to the largest bigint are user IDs."""
    with pool_factory(app_url).begin() as connection:
        with pytest.raises(ValueError, match="whole number from 1"):
            set_current_user(connection, cast(Any, user_id))
        assert recorded_user(connection) is None
