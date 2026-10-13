"""Tests for the core module's first migration: applying it, undoing it, its starting data."""

from collections.abc import Callable

from pydantic import SecretStr
from sqlalchemy import Connection, Engine, text

from bowerstone_engine.database_check import check_database
from bowerstone_engine.migration_runner.migrate import downgrade, upgrade
from bowerstone_engine.migration_runner.migration_files import Migration

CORE_TABLES = {
    "app_user",
    "applied_migration",
    "audit_log",
    "company",
    "event",
    "installed_app",
    "party",
    "permission",
    "role",
    "role_permission",
}

Refusal = Callable[..., str]


def core_tables(connection: Connection) -> set[str]:
    """Name the tables in the core schema.

    Args:
        connection: Any connection to the database.

    Returns:
        The table names, without the schema.

    Example:
        >>> sorted(core_tables(connection))[:2]
        ['app_user', 'applied_migration']
    """
    rows = connection.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = 'core'")
    ).all()
    return {row.tablename for row in rows}


def test_the_core_migration_is_found_in_the_package(
    core_migrations: list[Migration],
) -> None:
    """The SQL files ship inside bowerstone_engine.core.migrations and run in transactions."""
    assert [(m.version, m.name) for m in core_migrations] == [(1, "core")]
    assert not core_migrations[0].upgrade.no_transaction
    assert not core_migrations[0].downgrade.no_transaction


def test_upgrade_downgrade_and_upgrade_again(
    database_factory: Callable[[str], str],
    owner_url_for: Callable[[str], SecretStr],
    pool_factory: Callable[..., Engine],
    core_migrations: list[Migration],
) -> None:
    """Undoing the core migration leaves only the runner's table, and it applies again."""
    pool = pool_factory(owner_url_for(database_factory("builtin")))
    with pool.connect() as connection:
        assert [m.version for m in upgrade(connection, "core", core_migrations)] == [1]
        assert core_tables(connection) == CORE_TABLES
        connection.commit()

        assert [
            m.version for m in downgrade(connection, "core", core_migrations, 0)
        ] == [1]
        assert core_tables(connection) == {"applied_migration"}
        app_may_use_core = connection.execute(
            text("SELECT has_schema_privilege('bowerstone_app', 'core', 'USAGE')")
        ).scalar_one()
        assert app_may_use_core is False
        connection.commit()

        assert [m.version for m in upgrade(connection, "core", core_migrations)] == [1]
        assert core_tables(connection) == CORE_TABLES
        assert (
            connection.execute(text("SELECT count(*) FROM core.role")).scalar_one() == 3
        )


def test_the_database_check_passes_after_the_core_migration(
    as_owner: Connection,
) -> None:
    """The core migration keeps the database one Bowerstone may run against."""
    check_database(as_owner)


def test_the_starting_company(as_app: Connection) -> None:
    """One company row, with a starting name, CAD, and the Vancouver time zone."""
    rows = as_app.execute(
        text("SELECT id, name, currency, time_zone FROM core.company")
    ).all()
    assert [tuple(row) for row in rows] == [
        (1, "My Company", "CAD", "America/Vancouver")
    ]


def test_the_starting_roles_and_permissions(as_app: Connection) -> None:
    """Three roles, the four core permissions, and which role holds each."""
    roles = as_app.execute(text("SELECT key, name FROM core.role ORDER BY key")).all()
    assert [tuple(row) for row in roles] == [
        ("owner", "Owner"),
        ("read_only", "Read-only"),
        ("staff", "Staff"),
    ]
    permissions = as_app.execute(text("SELECT key, app_key FROM core.permission")).all()
    assert {tuple(row) for row in permissions} == {
        ("core.company", "core"),
        ("core.parties", "core"),
        ("core.users", "core"),
        ("core.view", "core"),
    }
    held = as_app.execute(
        text("SELECT role_key, permission_key FROM core.role_permission")
    ).all()
    assert {tuple(row) for row in held} == {
        ("owner", "core.company"),
        ("owner", "core.parties"),
        ("owner", "core.users"),
        ("owner", "core.view"),
        ("staff", "core.parties"),
        ("staff", "core.view"),
        ("read_only", "core.view"),
    }


def test_the_change_history_and_events_start_empty(as_app: Connection) -> None:
    """The starting data is not part of the change history, and no event is waiting."""
    assert as_app.execute(text("SELECT count(*) FROM core.audit_log")).scalar_one() == 0
    assert as_app.execute(text("SELECT count(*) FROM core.event")).scalar_one() == 0


def test_the_functions_run_with_a_fixed_search_path(as_owner: Connection) -> None:
    """Every core function pins its search path; only the two that write run as the owner."""
    rows = as_owner.execute(
        text(
            "SELECT p.proname, p.prosecdef, p.proconfig FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'core'"
        )
    ).all()
    assert {row.proname: row.prosecdef for row in rows} == {
        "audit_log_refuse_change": False,
        "company_check_time_zone": False,
        "emit_event": True,
        "write_audit": True,
    }
    assert {tuple(row.proconfig) for row in rows} == {
        ("search_path=pg_catalog, pg_temp",)
    }


def test_a_role_not_granted_emit_event_cannot_call_it(
    as_admin: Connection, refusal: Refusal
) -> None:
    """Every role may call a new function by default; the migration takes that away."""
    for statement in (
        "CREATE ROLE bowerstone_test_stranger NOLOGIN",
        "GRANT USAGE ON SCHEMA core TO bowerstone_test_stranger",
        "SET LOCAL ROLE bowerstone_test_stranger",
    ):
        as_admin.execute(text(statement))
    sqlstate = refusal(
        as_admin, "SELECT core.emit_event('core.test', 'core', 'core.party', '1')"
    )
    assert sqlstate == "42501"


def test_only_emit_event_is_granted_to_the_app_role(as_owner: Connection) -> None:
    """The app role may call emit_event and none of the trigger functions."""
    rows = as_owner.execute(
        text(
            "SELECT p.proname, has_function_privilege('bowerstone_app', p.oid, 'EXECUTE') "
            "AS granted FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'core'"
        )
    ).all()
    assert {row.proname: row.granted for row in rows} == {
        "audit_log_refuse_change": False,
        "company_check_time_zone": False,
        "emit_event": True,
        "write_audit": False,
    }
