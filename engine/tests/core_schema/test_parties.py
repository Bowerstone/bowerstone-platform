"""Tests for core.party, the shared customer and supplier list, and its history."""

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import Connection, text

from bowerstone_engine.db import set_current_user

Refusal = Callable[..., str]

ADD_PARTY = (
    "INSERT INTO core.party (name, email, phone, is_customer, is_supplier) "
    "VALUES (:name, :email, :phone, :is_customer, :is_supplier) RETURNING id"
)
A_PARTY = {
    "name": "Congo Textiles",
    "email": "orders@congo-textiles.example",
    "phone": "+1 604 555 0100",
    "is_customer": True,
    "is_supplier": False,
}


def add_party(connection: Connection, **changes: Any) -> int:
    """Add a party with made-up details, changed by the given values.

    Args:
        connection: The connection to add it on.
        changes: Values to use instead of A_PARTY's.

    Returns:
        The new party's id.

    Example:
        >>> add_party(as_app, name="Voodoo Mechanical")
        1
    """
    return int(connection.execute(text(ADD_PARTY), {**A_PARTY, **changes}).scalar_one())


def history_of(connection: Connection, party_id: int) -> list[Any]:
    """Read a party's change history, oldest first.

    Args:
        connection: The connection to read on.
        party_id: The party's id.

    Returns:
        The audit_log rows for that party.

    Example:
        >>> [row.action for row in history_of(as_app, 1)]
        ['insert', 'update']
    """
    return list(
        connection.execute(
            text(
                "SELECT user_id, action, before, after FROM core.audit_log "
                "WHERE record_type = 'core.party' AND record_id = :id ORDER BY id"
            ),
            {"id": str(party_id)},
        ).all()
    )


def test_the_app_role_may_add_change_and_deactivate_a_party(as_app: Connection) -> None:
    """The API adds parties, changes their details, and deactivates them."""
    party_id = add_party(as_app)
    as_app.execute(
        text(
            "UPDATE core.party SET phone = NULL, is_supplier = true, is_active = false WHERE id = :id"
        ),
        {"id": party_id},
    )
    row = as_app.execute(
        text(
            "SELECT name, phone, is_supplier, is_active, created_at FROM core.party WHERE id = :id"
        ),
        {"id": party_id},
    ).one()
    assert (row.name, row.phone, row.is_supplier, row.is_active) == (
        "Congo Textiles",
        None,
        True,
        False,
    )
    assert row.created_at is not None


def test_changes_to_parties_appear_in_the_history_with_their_user(
    as_app: Connection,
) -> None:
    """Adding and changing a party are both recorded, with who did it and the values."""
    set_current_user(as_app, 42)
    party_id = add_party(as_app)
    as_app.execute(
        text("UPDATE core.party SET name = 'Congo Textiles Inc.' WHERE id = :id"),
        {"id": party_id},
    )
    added, changed = history_of(as_app, party_id)
    assert (added.user_id, added.action, added.before) == (42, "insert", None)
    assert added.after["email"] == "orders@congo-textiles.example"
    assert (changed.user_id, changed.action) == (42, "update")
    assert changed.before["name"] == "Congo Textiles"
    assert changed.after["name"] == "Congo Textiles Inc."


def test_a_change_without_a_signed_in_user_records_no_user(as_app: Connection) -> None:
    """Before sign-in exists, the history records the change with no user."""
    party_id = add_party(as_app)
    assert [row.user_id for row in history_of(as_app, party_id)] == [None]


@pytest.mark.parametrize(
    "changes",
    [
        {"name": ""},
        {"name": "    "},
        {"name": "x" * 201},
        {"email": "no-at-sign"},
        {"email": "a@"},
        {"email": "@b"},
        {"email": "x" * 251 + "@b.io"},
        {"phone": ""},
        {"phone": "1" * 51},
    ],
)
def test_party_details_outside_the_limits_are_refused(
    as_app: Connection, refusal: Refusal, changes: dict[str, str]
) -> None:
    """Empty, blank, overlong, or address-less values fail the table's checks."""
    sqlstate = refusal(as_app, ADD_PARTY, **{**A_PARTY, **changes})
    assert sqlstate == "23514"


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "x" * 200},
        {"email": "x" * 249 + "@b.io"},
        {"phone": "1" * 50},
        {"email": None, "phone": None},
        {"name": "Société Générale du Québec 🙂"},
    ],
)
def test_party_details_at_the_limits_are_accepted(
    as_app: Connection, changes: dict[str, str | None]
) -> None:
    """The longest allowed values, missing contact details, and Unicode names are fine."""
    add_party(as_app, **changes)


def test_a_user_history_never_holds_the_password_hash(as_owner: Connection) -> None:
    """The history leaves password hashes out, before and after a change."""
    user_id = as_owner.execute(
        text(
            "INSERT INTO core.app_user (email, name, password_hash, role_key) "
            "VALUES ('ilia@example.com', 'Ilia', 'made-up-hash-1', 'owner') RETURNING id"
        )
    ).scalar_one()
    as_owner.execute(
        text(
            "UPDATE core.app_user SET password_hash = 'made-up-hash-2' WHERE id = :id"
        ),
        {"id": user_id},
    )
    rows = as_owner.execute(
        text(
            "SELECT before, after FROM core.audit_log WHERE record_type = 'core.app_user'"
        )
    ).all()
    assert len(rows) == 2
    for row in rows:
        for values in (row.before, row.after):
            assert values is None or "password_hash" not in values
    assert "made-up-hash" not in str([tuple(row) for row in rows])


def test_two_users_cannot_share_an_email_whatever_its_capitals(
    as_owner: Connection, refusal: Refusal
) -> None:
    """Email addresses are unique without regard to upper and lower case."""
    add_user = (
        "INSERT INTO core.app_user (email, name, password_hash, role_key) "
        "VALUES (:email, 'Someone', 'made-up-hash', 'staff')"
    )
    as_owner.execute(text(add_user), {"email": "Pat@Example.com"})
    assert refusal(as_owner, add_user, email="pat@example.COM") == "23505"
