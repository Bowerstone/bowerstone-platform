"""Database connections: a fixed-size pool, and recording who makes a change.

Every connection the pool opens starts with a statement timeout, a connect
timeout, and the UTC time zone. So no query runs without a limit, no
connection attempt waits forever, and dates behave the same in every run mode.
"""

from pydantic import SecretStr
from sqlalchemy import Connection, Engine, create_engine, text

from bowerstone_engine.config import ConnectionLimits

# The largest value of a PostgreSQL bigint, the type of user IDs.
LARGEST_USER_ID = 2**63 - 1


def create_connection_pool(database_url: SecretStr, limits: ConnectionLimits) -> Engine:
    """Create a fixed-size pool of connections to one database.

    The pool never opens more than limits.pool_size connections. A request that
    finds them all in use waits up to limits.pool_timeout_seconds, then fails
    with sqlalchemy.exc.TimeoutError.

    SQLAlchemy leaves bound values out of the error messages and logs it
    writes, because they can hold personal data. PostgreSQL's own message can
    still quote a value, such as the text that failed to read as a number, so
    the API must never show or log a database message as it is (task 1.5).

    Args:
        database_url: The database address, kept secret.
        limits: The timeouts and the pool size to use.

    Returns:
        A SQLAlchemy Engine, which hands out connections from the pool.

    Example:
        >>> pool = create_connection_pool(settings.database_url, settings)
        >>> with pool.begin() as connection:
        ...     connection.execute(text("SELECT 1")).scalar_one()
        1
    """
    session_options = (
        f"-c statement_timeout={limits.statement_timeout_ms} -c TimeZone=UTC"
    )
    return create_engine(
        database_url.get_secret_value(),
        pool_size=limits.pool_size,
        max_overflow=0,
        pool_timeout=limits.pool_timeout_seconds,
        hide_parameters=True,
        connect_args={
            "connect_timeout": limits.connect_timeout_seconds,
            "options": session_options,
        },
    )


def set_current_user(connection: Connection, user_id: int | None) -> None:
    """Record who is making the changes in the current transaction.

    The change history reads this value (core.write_audit, task 1.3). It lasts
    until the transaction ends, so a pooled connection never carries one
    request's user into the next.

    Args:
        connection: A connection with an open transaction.
        user_id: The signed-in user's ID, or None when no user is signed in,
            such as the development user before sign-in exists.

    Raises:
        RuntimeError: If the connection has no open transaction. Outside one,
            the value would be forgotten at once.
        ValueError: If user_id is not a whole number from 1 to the largest bigint.

    Example:
        >>> with pool.begin() as connection:
        ...     set_current_user(connection, 42)
    """
    if not connection.in_transaction():
        raise RuntimeError(
            "set_current_user needs an open transaction; outside one, "
            "the user would be forgotten at once"
        )
    if user_id is not None and (
        isinstance(user_id, bool)
        or not isinstance(user_id, int)
        or not 1 <= user_id <= LARGEST_USER_ID
    ):
        raise ValueError(
            f"a user ID must be a whole number from 1 to {LARGEST_USER_ID}"
        )
    connection.execute(
        text("SELECT set_config('bowerstone.user_id', :user_id, true)"),
        {"user_id": "" if user_id is None else str(user_id)},
    )
