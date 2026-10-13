"""Tests for bowerstone_engine.database_check: refusing the wrong database.

The logic is tested on made-up facts, including PostgreSQL versions this
server cannot have. The real cases run against PostgreSQL 18.
"""

from collections.abc import Callable
from dataclasses import replace

import pytest
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import Connection, Engine, text

from bowerstone_engine.config import APP_ROLE, OWNER_ROLE
from bowerstone_engine.database_check import (
    DatabaseFacts,
    check_database,
    find_problems,
    read_facts,
)
from bowerstone_engine.errors import DatabaseCheckError

GOOD_FACTS = DatabaseFacts(
    server_version_num=180006,
    locale_provider="b",
    locale="C.UTF-8",
    user_name=OWNER_ROLE,
    superuser_by_role={OWNER_ROLE: False, APP_ROLE: False},
)
WRONG_USER = "the connection acts as bowerstone_app, not bowerstone_owner"
SUPERUSER = "is a superuser, so it could switch off the ledger's safeguards"


def run(connection: Connection, statement: sql.Composed) -> None:
    """Run a statement that names roles, which cannot be bound parameters.

    Args:
        connection: The connection, inside its transaction.
        statement: The statement, with names quoted by psycopg's sql module.

    Raises:
        RuntimeError: If the connection has already been closed.

    Example:
        >>> run(admin_transaction, sql.SQL("SET LOCAL ROLE {}").format(name))
    """
    driver = connection.connection.driver_connection
    if driver is None:
        raise RuntimeError("the database connection has already been closed")
    driver.execute(statement)


def test_correct_facts_have_no_problems() -> None:
    """PostgreSQL 18, the built-in locale, both roles, and the owner pass."""
    assert find_problems(GOOD_FACTS) == []


@pytest.mark.parametrize(
    ("version", "problems"),
    [
        (180000, []),
        (189999, []),
        (170004, ["the server is PostgreSQL 17, not 18"]),
        (190000, ["the server is PostgreSQL 19, not 18"]),
        (100023, ["the server is PostgreSQL 10, not 18"]),
    ],
)
def test_only_postgresql_18_is_accepted(version: int, problems: list[str]) -> None:
    """Any 18.x release passes, and every other major version is refused."""
    assert find_problems(replace(GOOD_FACTS, server_version_num=version)) == problems


@pytest.mark.parametrize(
    ("provider", "locale", "problem"),
    [
        (
            "c",
            None,
            "the database uses the operating system's locale, not the built-in C.UTF-8",
        ),
        ("i", "und", "the database uses the ICU locale und, not the built-in C.UTF-8"),
        ("b", "C", "the database uses the built-in locale C, not the built-in C.UTF-8"),
        (
            "b",
            "PG_UNICODE_FAST",
            "the database uses the built-in locale PG_UNICODE_FAST, "
            "not the built-in C.UTF-8",
        ),
    ],
)
def test_any_other_locale_is_refused(
    provider: str, locale: str | None, problem: str
) -> None:
    """Only the built-in C.UTF-8 locale passes, and the message names the one in use."""
    facts = replace(GOOD_FACTS, locale_provider=provider, locale=locale)
    assert find_problems(facts) == [problem]


@pytest.mark.parametrize(
    ("roles", "problems"),
    [
        ({APP_ROLE: False}, ["the role bowerstone_owner does not exist"]),
        ({OWNER_ROLE: False}, ["the role bowerstone_app does not exist"]),
        (
            {},
            [
                "the role bowerstone_owner does not exist",
                "the role bowerstone_app does not exist",
            ],
        ),
        (
            {OWNER_ROLE: True, APP_ROLE: False},
            [f"the role bowerstone_owner {SUPERUSER}"],
        ),
        (
            {OWNER_ROLE: False, APP_ROLE: True},
            [f"the role bowerstone_app {SUPERUSER}"],
        ),
    ],
)
def test_missing_or_superuser_roles_are_refused(
    roles: dict[str, bool], problems: list[str]
) -> None:
    """Both roles must exist, and neither may be a superuser."""
    assert find_problems(replace(GOOD_FACTS, superuser_by_role=roles)) == problems


@pytest.mark.parametrize("user", [APP_ROLE, "bowerstone_admin", "postgres"])
def test_acting_as_any_role_but_the_owner_is_refused(user: str) -> None:
    """The check passes only when the connection acts as bowerstone_owner."""
    assert find_problems(replace(GOOD_FACTS, user_name=user)) == [
        f"the connection acts as {user}, not bowerstone_owner"
    ]


def test_every_problem_is_reported_at_once() -> None:
    """One run lists everything that needs fixing, in a fixed order."""
    facts = DatabaseFacts(170004, "c", None, "postgres", {})
    assert find_problems(facts) == [
        "the server is PostgreSQL 17, not 18",
        "the database uses the operating system's locale, not the built-in C.UTF-8",
        "the role bowerstone_owner does not exist",
        "the role bowerstone_app does not exist",
        "the connection acts as postgres, not bowerstone_owner",
    ]


def test_the_error_names_every_problem() -> None:
    """The error's message and its problems list carry each reason."""
    error = DatabaseCheckError(["first reason", "second reason"])
    assert error.problems == ("first reason", "second reason")
    assert str(error) == "The database check failed: first reason; second reason."


def test_an_error_without_problems_is_refused() -> None:
    """A check error must say why, so an empty list is a mistake."""
    with pytest.raises(ValueError, match="at least one problem"):
        DatabaseCheckError([])


def test_the_facts_of_the_test_database(owner_connection: Connection) -> None:
    """The facts come straight from PostgreSQL 18 and the test database."""
    facts = read_facts(owner_connection)
    assert facts.server_version_num // 10_000 == 18
    assert (facts.locale_provider, facts.locale) == ("b", "C.UTF-8")
    assert facts.user_name == OWNER_ROLE
    assert facts.superuser_by_role == {OWNER_ROLE: False, APP_ROLE: False}


def test_the_check_passes_as_the_owner(owner_connection: Connection) -> None:
    """A correctly set-up database, reached as bowerstone_owner, passes."""
    check_database(owner_connection)


def test_the_check_refuses_the_app_role(app_connection: Connection) -> None:
    """The running API's role may not run migrations."""
    with pytest.raises(DatabaseCheckError) as refused:
        check_database(app_connection)
    assert refused.value.problems == (WRONG_USER,)


def test_the_check_refuses_the_operating_systems_locale(
    database_factory: Callable[[str], str],
    owner_url_for: Callable[[str], SecretStr],
    pool_factory: Callable[..., Engine],
) -> None:
    """A database made with the operating system's locale is refused."""
    database = database_factory("libc")
    pool = pool_factory(owner_url_for(database))
    with pool.connect() as connection, pytest.raises(DatabaseCheckError) as refused:
        check_database(connection)
    assert refused.value.problems == (
        "the database uses the operating system's locale, not the built-in C.UTF-8",
    )


@pytest.mark.parametrize("role", [OWNER_ROLE, APP_ROLE])
def test_the_check_refuses_a_missing_role(
    admin_transaction: Connection, role: str
) -> None:
    """A missing role is refused. It is renamed in a transaction that is rolled back."""
    hidden = f"{role}_hidden_by_test"
    run(
        admin_transaction,
        sql.SQL("ALTER ROLE {} RENAME TO {}").format(
            sql.Identifier(role), sql.Identifier(hidden)
        ),
    )
    acting_as = hidden if role == OWNER_ROLE else OWNER_ROLE
    run(
        admin_transaction,
        sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(acting_as)),
    )
    with pytest.raises(DatabaseCheckError) as refused:
        check_database(admin_transaction)
    assert f"the role {role} does not exist" in refused.value.problems

    admin_transaction.rollback()
    restored = admin_transaction.execute(
        text("SELECT count(*) FROM pg_roles WHERE rolname = :role"), {"role": role}
    ).scalar_one()
    assert restored == 1


def test_the_check_refuses_a_superuser_role(admin_transaction: Connection) -> None:
    """A Bowerstone role with superuser rights is refused."""
    run(
        admin_transaction,
        sql.SQL("ALTER ROLE {} SUPERUSER").format(sql.Identifier(APP_ROLE)),
    )
    run(
        admin_transaction,
        sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(OWNER_ROLE)),
    )
    with pytest.raises(DatabaseCheckError) as refused:
        check_database(admin_transaction)
    assert refused.value.problems == (f"the role bowerstone_app {SUPERUSER}",)
