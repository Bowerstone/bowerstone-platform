"""Tests for core.audit_log and core.write_audit: an append-only change history."""

from collections.abc import Callable

import pytest
from sqlalchemy import Connection, exc, text

Refusal = Callable[..., str]


def add_party_as_owner(connection: Connection) -> None:
    """Add a party, so the change history has a row to try to change.

    Args:
        connection: A connection as the owner role.

    Example:
        >>> add_party_as_owner(as_owner)
    """
    connection.execute(text("INSERT INTO core.party (name) VALUES ('Someone Ltd.')"))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE core.audit_log SET user_id = 99",
        "DELETE FROM core.audit_log",
        "TRUNCATE core.audit_log",
    ],
)
def test_not_even_the_owner_may_change_or_empty_the_history(
    as_owner: Connection, refusal: Refusal, statement: str
) -> None:
    """The owner role owns the table, but its triggers still refuse with BW012."""
    add_party_as_owner(as_owner)
    assert refusal(as_owner, statement) == "BW012"


def test_tables_in_other_schemas_are_told_apart(as_owner: Connection) -> None:
    """An app's table with the same name as a core table is recorded as schema.table."""
    for statement in (
        "CREATE SCHEMA demo",
        "CREATE TABLE demo.party (id bigint PRIMARY KEY, name text)",
        "CREATE TRIGGER audit_demo_party AFTER INSERT OR UPDATE OR DELETE ON demo.party "
        "FOR EACH ROW EXECUTE FUNCTION core.write_audit()",
        "INSERT INTO demo.party VALUES (1, 'Demo')",
        "DELETE FROM demo.party",
    ):
        as_owner.execute(text(statement))
    add_party_as_owner(as_owner)
    rows = as_owner.execute(
        text("SELECT action, record_type, record_id FROM core.audit_log ORDER BY id")
    ).all()
    assert [tuple(row)[:2] for row in rows] == [
        ("insert", "demo.party"),
        ("delete", "demo.party"),
        ("insert", "core.party"),
    ]
    assert rows[0].record_id == "1"


def test_write_audit_refuses_a_table_without_an_id(as_owner: Connection) -> None:
    """Attaching the history to a table with no id column fails loudly on first use."""
    for statement in (
        "CREATE TABLE core.no_id (code text PRIMARY KEY)",
        "CREATE TRIGGER audit_no_id AFTER INSERT ON core.no_id "
        "FOR EACH ROW EXECUTE FUNCTION core.write_audit()",
    ):
        as_owner.execute(text(statement))
    savepoint = as_owner.begin_nested()
    with pytest.raises(
        exc.ProgrammingError, match=r"needs an id column on core\.no_id"
    ):
        as_owner.execute(text("INSERT INTO core.no_id VALUES ('x')"))
    savepoint.rollback()


def test_a_records_history_is_found_through_its_index(as_owner: Connection) -> None:
    """With 200,000 rows, one record's history is read through audit_log_record_idx."""
    as_owner.execute(
        text(
            "INSERT INTO core.audit_log (action, record_type, record_id, after) "
            "SELECT 'insert', 'core.party', g::text, '{}'::jsonb "
            "FROM generate_series(1, 200000) AS g"
        )
    )
    as_owner.execute(text("ANALYZE core.audit_log"))
    plan = as_owner.execute(
        text(
            "EXPLAIN (ANALYZE, FORMAT TEXT) SELECT * FROM core.audit_log "
            "WHERE record_type = 'core.party' AND record_id = '12345' ORDER BY id"
        )
    ).all()
    plan_text = "\n".join(row[0] for row in plan)
    assert "audit_log_record_idx" in plan_text, plan_text
