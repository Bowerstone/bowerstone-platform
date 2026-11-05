"""The database check refuses any database the engine may not migrate or serve.

Some tests change a role: they rename it, or make it a superuser. Role changes
in Postgres are transactional, so those tests make the change as the admin
inside a transaction, switch to the role being checked with SET LOCAL ROLE,
run the check in that same transaction, and roll it back. Nothing outside the
test ever sees the change.
"""

from collections.abc import Callable

import pytest
from sqlalchemy import Connection, text

from bowerstone_engine.db import APP_ROLE, OWNER_ROLE
from bowerstone_engine.migrate import (
    DatabaseCheckError,
    MigrationRef,
    check_before_migrating,
    check_before_serving,
    create_record_table,
)
from bowerstone_engine.testing import PostgresServer, ScratchDatabase

CORE_1 = MigrationRef("core", 1, "core")
EXAMPLE_1 = MigrationRef("example", 1, "example")

WRONG_LOCALES = [
    pytest.param(
        "LOCALE_PROVIDER libc LOCALE 'C.UTF-8'", "the libc locale C.UTF-8", id="libc"
    ),
    pytest.param(
        "LOCALE_PROVIDER builtin BUILTIN_LOCALE 'C' LOCALE 'C'",
        "the built-in locale C",
        id="built-in C",
    ),
    pytest.param(
        "LOCALE_PROVIDER icu ICU_LOCALE 'und' LOCALE 'C.UTF-8'",
        "the ICU locale und",
        id="ICU",
    ),
]


def problems_of(check: Callable[[], None]) -> tuple[str, ...]:
    with pytest.raises(DatabaseCheckError) as error:
        check()
    return error.value.problems


def check_as_migrator(conn: Connection) -> Callable[[], None]:
    return lambda: check_before_migrating(conn)


def check_as_server(
    conn: Connection, expected: list[MigrationRef] | None = None
) -> Callable[[], None]:
    return lambda: check_before_serving(conn, expected or [])


def record_applied(database: ScratchDatabase, *refs: MigrationRef) -> None:
    """Creates the record table as the owner, and records `refs` as applied."""
    with database.connect(OWNER_ROLE) as conn:
        create_record_table(conn)
        for ref in refs:
            conn.execute(
                text(
                    "INSERT INTO core.applied_migration"
                    " (module, version, name, checksum)"
                    " VALUES (:module, :version, :name, 'test')"
                ),
                {"module": ref.module, "version": ref.version, "name": ref.name},
            )
        conn.commit()


# A correct database passes.


def test_the_owner_form_accepts_a_correct_database(database: ScratchDatabase) -> None:
    with database.connect(OWNER_ROLE) as conn:
        check_before_migrating(conn)


def test_the_app_form_accepts_a_correct_database_with_every_migration_applied(
    database: ScratchDatabase,
) -> None:
    record_applied(database, CORE_1, EXAMPLE_1)
    with database.connect(APP_ROLE) as conn:
        check_before_serving(conn, [CORE_1, EXAMPLE_1])


# The wrong version.

PG17_PROBLEM = (
    "The database server runs PostgreSQL 17, and Bowerstone needs PostgreSQL 18.",
)


def test_the_owner_form_refuses_postgres_17(server_pg17: PostgresServer) -> None:
    with (
        server_pg17.scratch_database() as database,
        database.connect(OWNER_ROLE) as conn,
    ):
        assert problems_of(check_as_migrator(conn)) == PG17_PROBLEM


def test_the_app_form_refuses_postgres_17(server_pg17: PostgresServer) -> None:
    with server_pg17.scratch_database() as database, database.connect(APP_ROLE) as conn:
        assert problems_of(check_as_server(conn)) == PG17_PROBLEM


# The wrong locale.


def locale_problem(description: str) -> tuple[str, ...]:
    return (
        f"The database uses {description}, "
        "and Bowerstone needs the built-in locale C.UTF-8.",
    )


@pytest.mark.parametrize(("locale", "description"), WRONG_LOCALES)
def test_the_owner_form_refuses_the_wrong_locale(
    server: PostgresServer, locale: str, description: str
) -> None:
    with (
        server.scratch_database(locale) as database,
        database.connect(OWNER_ROLE) as conn,
    ):
        assert problems_of(check_as_migrator(conn)) == locale_problem(description)


@pytest.mark.parametrize(("locale", "description"), WRONG_LOCALES)
def test_the_app_form_refuses_the_wrong_locale(
    server: PostgresServer, locale: str, description: str
) -> None:
    with (
        server.scratch_database(locale) as database,
        database.connect(APP_ROLE) as conn,
    ):
        assert problems_of(check_as_server(conn)) == locale_problem(description)


# Missing roles, and roles that are superusers.


def test_the_owner_form_refuses_a_missing_app_role(database: ScratchDatabase) -> None:
    with database.connect_as_admin() as conn:
        conn.execute(text(f"ALTER ROLE {APP_ROLE} RENAME TO {APP_ROLE}_gone"))
        conn.execute(text(f"SET LOCAL ROLE {OWNER_ROLE}"))
        found = problems_of(check_as_migrator(conn))
        conn.rollback()
    assert found == (f"The role {APP_ROLE} does not exist.",)


def test_the_owner_form_refuses_a_missing_owner_role(database: ScratchDatabase) -> None:
    with database.connect_as_admin() as conn:
        conn.execute(text(f"ALTER ROLE {OWNER_ROLE} RENAME TO {OWNER_ROLE}_gone"))
        conn.execute(text(f"SET LOCAL ROLE {OWNER_ROLE}_gone"))
        found = problems_of(check_as_migrator(conn))
        conn.rollback()
    assert found == (
        f"The role {OWNER_ROLE} does not exist.",
        f"The connection is signed in as {OWNER_ROLE}_gone, "
        f"and migrations must run as {OWNER_ROLE}.",
    )


@pytest.mark.parametrize("role", [OWNER_ROLE, APP_ROLE])
def test_the_owner_form_refuses_a_superuser_role(
    database: ScratchDatabase, role: str
) -> None:
    with database.connect_as_admin() as conn:
        conn.execute(text(f"ALTER ROLE {role} SUPERUSER"))
        conn.execute(text(f"SET LOCAL ROLE {OWNER_ROLE}"))
        found = problems_of(check_as_migrator(conn))
        conn.rollback()
    assert found == (
        f"The role {role} is a superuser, and Bowerstone's roles must not be.",
    )


def test_the_app_form_refuses_an_app_role_that_is_a_superuser(
    database: ScratchDatabase,
) -> None:
    with database.connect_as_admin() as conn:
        conn.execute(text(f"ALTER ROLE {APP_ROLE} SUPERUSER"))
        conn.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))
        found = problems_of(check_as_server(conn))
        conn.rollback()
    assert found == (
        f"The role {APP_ROLE} is a superuser, and Bowerstone's roles must not be.",
    )


# The wrong user.


@pytest.mark.parametrize("role", [APP_ROLE, "admin"])
def test_the_owner_form_refuses_any_user_but_the_owner(
    database: ScratchDatabase, role: str
) -> None:
    signed_in_as = database.server.admin_name if role == "admin" else role
    with database.connect(signed_in_as) as conn:
        assert problems_of(check_as_migrator(conn)) == (
            f"The connection is signed in as {signed_in_as}, "
            f"and migrations must run as {OWNER_ROLE}.",
        )


@pytest.mark.parametrize("role", [OWNER_ROLE, "admin"])
def test_the_app_form_refuses_any_user_but_the_app_role(
    database: ScratchDatabase, role: str
) -> None:
    signed_in_as = database.server.admin_name if role == "admin" else role
    with database.connect(signed_in_as) as conn:
        assert problems_of(check_as_server(conn)) == (
            f"The connection is signed in as {signed_in_as}, "
            f"and the engine must serve as {APP_ROLE}.",
        )


# Missing migrations, before serving.


def test_the_app_form_refuses_a_database_that_was_never_migrated(
    database: ScratchDatabase,
) -> None:
    with database.connect(APP_ROLE) as conn:
        assert problems_of(check_as_server(conn, [CORE_1, EXAMPLE_1])) == (
            "These migrations have not been applied: "
            "core 0001_core, example 0001_example. Run bowerstone migrate first.",
        )


def test_the_app_form_refuses_a_missing_migration(database: ScratchDatabase) -> None:
    record_applied(database, CORE_1)
    with database.connect(APP_ROLE) as conn:
        assert problems_of(check_as_server(conn, [CORE_1, EXAMPLE_1])) == (
            "This migration has not been applied: example 0001_example. "
            "Run bowerstone migrate first.",
        )


def test_the_app_form_refuses_when_it_cannot_read_the_record(
    database: ScratchDatabase,
) -> None:
    record_applied(database, CORE_1)
    with database.connect(OWNER_ROLE) as conn:
        conn.execute(
            text("REVOKE SELECT ON core.applied_migration FROM bowerstone_app")
        )
        conn.commit()
    with database.connect(APP_ROLE) as conn:
        assert problems_of(check_as_server(conn, [CORE_1])) == (
            f"The role {APP_ROLE} cannot read core.applied_migration.",
        )
