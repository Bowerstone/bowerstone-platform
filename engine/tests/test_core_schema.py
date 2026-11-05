"""The core schema: what the app role may and may not do, the history, and events.

These tests talk to the database directly, not through the API. Their job is
to show that the database refuses a wrong change even if the engine has a bug.
"""

from collections.abc import Mapping
from typing import Any

import psycopg
import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from bowerstone_engine.db import APP_ROLE, OWNER_ROLE, set_current_user
from bowerstone_engine.migrate import (
    check_before_serving,
    core_migrations,
    downgrade,
    upgrade,
)
from bowerstone_engine.testing import ScratchDatabase

INSUFFICIENT_PRIVILEGE = "42501"
CHECK_VIOLATION = "23514"
UNIQUE_VIOLATION = "23505"
INVALID_PARAMETER = "22023"
AUDIT_LOG_IS_APPEND_ONLY = "BW012"

CORE_TABLES = [
    "core.company",
    "core.role",
    "core.permission",
    "core.role_permission",
    "core.app_user",
    "core.installed_product",
    "core.audit_log",
    "core.event",
]


def sqlstate_of(
    conn: Connection, sql: str, params: Mapping[str, Any] | None = None
) -> str:
    """Runs `sql`, expecting Postgres to refuse it, and returns the error's SQLSTATE."""
    with pytest.raises(DBAPIError) as error:
        conn.execute(text(sql), params or {})
    conn.rollback()
    assert isinstance(error.value.orig, psycopg.Error)
    return str(error.value.orig.sqlstate)


def rows(database: ScratchDatabase, sql: str) -> list[dict[str, Any]]:
    with database.connect(OWNER_ROLE) as conn:
        return [dict(row) for row in conn.execute(text(sql)).mappings()]


def audit_log(database: ScratchDatabase) -> list[dict[str, Any]]:
    return rows(
        database,
        "SELECT user_id, action, record_type, record_id, before, after "
        "FROM core.audit_log ORDER BY id",
    )


# The starting data.


def test_the_core_starts_with_one_company_three_roles_and_their_permissions(
    core_db: ScratchDatabase,
) -> None:
    assert rows(core_db, "SELECT * FROM core.company") == [
        {"id": 1, "name": "My Company", "currency": "CAD", "time_zone": "UTC"}
    ]
    assert rows(
        core_db,
        "SELECT role_key, array_agg(permission_key ORDER BY permission_key) AS keys "
        "FROM core.role_permission GROUP BY role_key ORDER BY role_key",
    ) == [
        {"role_key": "owner", "keys": ["core.company", "core.users", "core.view"]},
        {"role_key": "read_only", "keys": ["core.view"]},
        {"role_key": "staff", "keys": ["core.view"]},
    ]
    for table in (
        "core.app_user",
        "core.installed_product",
        "core.audit_log",
        "core.event",
    ):
        assert rows(core_db, f"SELECT count(*) AS n FROM {table}") == [{"n": 0}]  # noqa: S608


def test_the_engine_may_serve_once_the_core_is_migrated(
    core_db: ScratchDatabase,
) -> None:
    with core_db.connect(APP_ROLE) as conn:
        check_before_serving(conn, core_migrations().refs)


def test_downgrading_the_core_leaves_the_runners_record(
    core_db: ScratchDatabase,
) -> None:
    engine = core_db.engine(OWNER_ROLE)
    try:
        downgrade(engine, core_migrations())
        assert rows(
            core_db,
            "SELECT array_agg(table_name::text ORDER BY table_name) AS tables "
            "FROM information_schema.tables WHERE table_schema = 'core'",
        ) == [{"tables": ["applied_migration"]}]
        assert [str(m) for m in upgrade(engine, [core_migrations()])] == [
            "core 0001_core"
        ]
    finally:
        engine.dispose()


# What the app role may not do.

APP_ROLE_REFUSALS = [
    pytest.param(
        "INSERT INTO core.audit_log (action, record_type, record_id) "
        "VALUES ('insert', 'core.company', '1')",
        id="write the audit log",
    ),
    pytest.param(
        "UPDATE core.audit_log SET record_id = 'forged'", id="change the audit log"
    ),
    pytest.param("DELETE FROM core.audit_log", id="delete from the audit log"),
    pytest.param(
        "INSERT INTO core.event (type, source, record_type, record_id) "
        "VALUES ('core.forged', 'core', 'core.company', '1')",
        id="write an event",
    ),
    pytest.param(
        "UPDATE core.event SET handled_at = now()", id="mark an event handled"
    ),
    pytest.param("DELETE FROM core.event", id="delete an event"),
    pytest.param(
        "INSERT INTO core.installed_product (key, version) VALUES ('forged', '1.0.0')",
        id="install a product",
    ),
    pytest.param(
        "UPDATE core.installed_product SET is_enabled = false", id="disable a product"
    ),
    pytest.param("DELETE FROM core.installed_product", id="remove a product"),
    pytest.param(
        "INSERT INTO core.permission (key, description, module) "
        "VALUES ('core.forged', 'Forged', 'core')",
        id="add a permission",
    ),
    pytest.param(
        "INSERT INTO core.role_permission VALUES ('read_only', 'core.users')",
        id="grant a permission",
    ),
    pytest.param("DELETE FROM core.role_permission", id="take permissions away"),
    pytest.param("INSERT INTO core.role VALUES ('admin', 'Admin')", id="add a role"),
    pytest.param(
        "INSERT INTO core.app_user (email, name, password_hash, role_key) "
        "VALUES ('a@example.com', 'A', 'x', 'owner')",
        id="add a user",
    ),
    pytest.param(
        "INSERT INTO core.company (name) VALUES ('Second')", id="add a company"
    ),
    pytest.param("DELETE FROM core.company", id="delete the company"),
    pytest.param("UPDATE core.company SET id = 1", id="change the company's id"),
    pytest.param(
        "ALTER TABLE core.company DISABLE TRIGGER audit", id="switch off a trigger"
    ),
    pytest.param(
        "ALTER TABLE core.audit_log DISABLE TRIGGER append_only",
        id="switch off the audit log's guard",
    ),
    pytest.param(
        "SET session_replication_role = replica", id="switch off every trigger at once"
    ),
    pytest.param("DROP TABLE core.audit_log", id="drop a table"),
    pytest.param("DROP FUNCTION core.write_audit()", id="drop the audit function"),
    pytest.param("CREATE TABLE core.forged (id int)", id="create a table in core"),
    pytest.param("CREATE TABLE public.forged (id int)", id="create a table in public"),
    pytest.param(
        "CREATE TEMPORARY TABLE forged (id int)", id="create a temporary table"
    ),
    *(pytest.param(f"TRUNCATE {table}", id=f"empty {table}") for table in CORE_TABLES),
]


@pytest.mark.parametrize("sql", APP_ROLE_REFUSALS)
def test_the_app_role_is_refused(core_db: ScratchDatabase, sql: str) -> None:
    # A history row exists, so a refused UPDATE or DELETE has a row to aim at.
    with core_db.connect(APP_ROLE) as conn:
        conn.execute(text("UPDATE core.company SET name = 'Has history'"))
        conn.commit()
        assert sqlstate_of(conn, sql) == INSUFFICIENT_PRIVILEGE
    assert len(audit_log(core_db)) == 1


def test_the_app_role_cannot_call_the_audit_function_or_the_guard(
    core_db: ScratchDatabase,
) -> None:
    privileges = rows(
        core_db,
        "SELECT proname::text AS name, "
        "has_function_privilege('bowerstone_app', oid, 'EXECUTE') AS can_run "
        "FROM pg_proc WHERE pronamespace = 'core'::regnamespace ORDER BY proname",
    )
    assert privileges == [
        {"name": "emit_event", "can_run": True},
        {"name": "refuse_audit_change", "can_run": False},
        {"name": "write_audit", "can_run": False},
    ]


# What the app role may do.


@pytest.mark.parametrize("table", CORE_TABLES)
def test_the_app_role_reads_every_core_table(
    core_db: ScratchDatabase, table: str
) -> None:
    with core_db.connect(APP_ROLE) as conn:
        conn.execute(text(f"SELECT * FROM {table}"))  # noqa: S608


def test_the_app_role_changes_the_company_details(core_db: ScratchDatabase) -> None:
    with core_db.connect(APP_ROLE) as conn:
        conn.execute(
            text(
                "UPDATE core.company SET name = 'Bowerstone Test Ltd', "
                "currency = 'USD', time_zone = 'America/Toronto'"
            )
        )
        conn.commit()
    assert rows(core_db, "SELECT name, currency, time_zone FROM core.company") == [
        {
            "name": "Bowerstone Test Ltd",
            "currency": "USD",
            "time_zone": "America/Toronto",
        }
    ]


@pytest.mark.parametrize(
    ("sql", "sqlstate"),
    [
        pytest.param(
            "UPDATE core.company SET name = '   '", CHECK_VIOLATION, id="blank name"
        ),
        pytest.param(
            "UPDATE core.company SET currency = 'cad'",
            CHECK_VIOLATION,
            id="lower-case currency",
        ),
        pytest.param(
            "UPDATE core.company SET currency = 'CADX'",
            CHECK_VIOLATION,
            id="four-letter currency",
        ),
        pytest.param(
            "UPDATE core.company SET time_zone = 'Mars/Olympus'",
            INVALID_PARAMETER,
            id="unknown time zone",
        ),
    ],
)
def test_wrong_company_details_are_refused(
    core_db: ScratchDatabase, sql: str, sqlstate: str
) -> None:
    with core_db.connect(APP_ROLE) as conn:
        assert sqlstate_of(conn, sql) == sqlstate


def test_there_is_only_ever_one_company(core_db: ScratchDatabase) -> None:
    with core_db.connect(OWNER_ROLE) as conn:
        assert sqlstate_of(conn, "INSERT INTO core.company (name) VALUES ('Two')") == (
            UNIQUE_VIOLATION
        )
        assert sqlstate_of(
            conn, "INSERT INTO core.company (id, name) VALUES (2, 'Two')"
        ) == (CHECK_VIOLATION)


# The change history.


def test_a_change_to_the_company_appears_in_the_audit_log(
    core_db: ScratchDatabase,
) -> None:
    with core_db.connect(APP_ROLE) as conn:
        set_current_user(conn, 7)
        conn.execute(text("UPDATE core.company SET name = 'Bowerstone Test Ltd'"))
        conn.commit()

    [entry] = audit_log(core_db)
    assert entry["user_id"] == 7
    assert (entry["action"], entry["record_type"], entry["record_id"]) == (
        "update",
        "core.company",
        "1",
    )
    assert entry["before"]["name"] == "My Company"
    assert entry["after"]["name"] == "Bowerstone Test Ltd"


def test_a_change_with_no_user_set_records_no_user(core_db: ScratchDatabase) -> None:
    with core_db.connect(APP_ROLE) as conn:
        conn.execute(text("UPDATE core.company SET time_zone = 'America/Toronto'"))
        conn.commit()

    [entry] = audit_log(core_db)
    assert entry["user_id"] is None


def test_the_audit_log_is_append_only_even_for_the_owner(
    core_db: ScratchDatabase,
) -> None:
    with core_db.connect(APP_ROLE) as conn:
        conn.execute(text("UPDATE core.company SET name = 'Has history'"))
        conn.commit()

    with core_db.connect(OWNER_ROLE) as conn:
        for sql in (
            "UPDATE core.audit_log SET user_id = 1",
            "DELETE FROM core.audit_log",
            "TRUNCATE core.audit_log",
        ):
            assert sqlstate_of(conn, sql) == AUDIT_LOG_IS_APPEND_ONLY
    assert len(audit_log(core_db)) == 1


def test_the_audit_log_leaves_out_password_hashes(core_db: ScratchDatabase) -> None:
    with core_db.connect(OWNER_ROLE) as conn:
        user_id = conn.execute(
            text(
                "INSERT INTO core.app_user (email, name, password_hash, role_key) "
                "VALUES ('pat@example.com', 'Pat', 'secret-hash', 'staff') RETURNING id"
            )
        ).scalar_one()
        conn.execute(text("UPDATE core.app_user SET password_hash = 'new-secret-hash'"))
        conn.commit()

    entries = audit_log(core_db)
    assert [(e["action"], e["record_type"], e["record_id"]) for e in entries] == [
        ("insert", "core.app_user", str(user_id)),
        ("update", "core.app_user", str(user_id)),
    ]
    for entry in entries:
        for values in (entry["before"], entry["after"]):
            assert values is None or (
                "password_hash" not in values and "email" in values
            )


def test_tables_without_an_id_name_their_key_columns(core_db: ScratchDatabase) -> None:
    with core_db.connect(OWNER_ROLE) as conn:
        conn.execute(text("INSERT INTO core.role VALUES ('auditor', 'Auditor')"))
        conn.execute(
            text("INSERT INTO core.role_permission VALUES ('auditor', 'core.view')")
        )
        conn.execute(text("DELETE FROM core.role WHERE key = 'auditor'"))
        conn.commit()

    entries = [
        (e["action"], e["record_type"], e["record_id"]) for e in audit_log(core_db)
    ]
    assert entries[:2] == [
        ("insert", "core.role", "auditor"),
        ("insert", "core.role_permission", "auditor/core.view"),
    ]
    # Deleting the role cascades to its permission. Postgres picks the order.
    assert set(entries[2:]) == {
        ("delete", "core.role", "auditor"),
        ("delete", "core.role_permission", "auditor/core.view"),
    }


def test_the_audit_function_refuses_a_table_with_no_key(
    core_db: ScratchDatabase,
) -> None:
    with core_db.connect(OWNER_ROLE) as conn:
        conn.execute(text("CREATE TABLE public.no_key (code text)"))
        conn.execute(
            text(
                "CREATE TRIGGER audit AFTER INSERT ON public.no_key "
                "FOR EACH ROW EXECUTE FUNCTION core.write_audit()"
            )
        )
        with pytest.raises(DBAPIError, match="has no value in its key column"):
            conn.execute(text("INSERT INTO public.no_key VALUES ('x')"))


def test_the_definer_functions_cannot_be_redirected(core_db: ScratchDatabase) -> None:
    # SECURITY DEFINER functions run as their owner, so each pins its search path.
    assert rows(
        core_db,
        "SELECT proname::text AS name, prosecdef AS definer, proconfig AS config "
        "FROM pg_proc WHERE pronamespace = 'core'::regnamespace AND prosecdef "
        "ORDER BY proname",
    ) == [
        {
            "name": "emit_event",
            "definer": True,
            "config": ["search_path=pg_catalog, pg_temp"],
        },
        {
            "name": "write_audit",
            "definer": True,
            "config": ["search_path=pg_catalog, pg_temp"],
        },
    ]


# Events.


def test_emit_event_writes_an_event(core_db: ScratchDatabase) -> None:
    with core_db.connect(APP_ROLE) as conn:
        event_id = conn.execute(
            text(
                "SELECT core.emit_event('core.company_renamed', 'core', "
                "'core.company', '1', '{\"name\": \"Bowerstone Test Ltd\"}')"
            )
        ).scalar_one()
        default_id = conn.execute(
            text("SELECT core.emit_event('core.checked', 'core', 'core.company', '1')")
        ).scalar_one()
        conn.commit()

    events = rows(
        core_db,
        "SELECT id, type, source, record_type, record_id, payload, handled_at "
        "FROM core.event ORDER BY id",
    )
    assert events == [
        {
            "id": event_id,
            "type": "core.company_renamed",
            "source": "core",
            "record_type": "core.company",
            "record_id": "1",
            "payload": {"name": "Bowerstone Test Ltd"},
            "handled_at": None,
        },
        {
            "id": default_id,
            "type": "core.checked",
            "source": "core",
            "record_type": "core.company",
            "record_id": "1",
            "payload": {},
            "handled_at": None,
        },
    ]


def test_an_event_that_is_rolled_back_is_not_kept(core_db: ScratchDatabase) -> None:
    with core_db.connect(APP_ROLE) as conn:
        conn.execute(
            text("SELECT core.emit_event('core.tried', 'core', 'core.company', '1')")
        )
        conn.rollback()
    assert rows(core_db, "SELECT count(*) AS n FROM core.event") == [{"n": 0}]


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(
            "core.emit_event('accounting.entry_posted', 'core', 'core.company', '1')",
            id="a type from another source",
        ),
        pytest.param(
            "core.emit_event('core.listed', 'core', 'core.company', '1', '[1, 2]')",
            id="a payload that is not an object",
        ),
    ],
)
def test_emit_event_refuses_a_malformed_event(
    core_db: ScratchDatabase, call: str
) -> None:
    with core_db.connect(APP_ROLE) as conn:
        assert sqlstate_of(conn, f"SELECT {call}") == CHECK_VIOLATION
