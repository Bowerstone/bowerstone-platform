"""Tests for core.company: one row, a real time zone, and a fixed currency."""

from collections.abc import Callable

import pytest
from sqlalchemy import Connection, text

from bowerstone_engine.db import set_current_user

Refusal = Callable[..., str]
UPDATE_NAME = "UPDATE core.company SET name = :name"


def test_the_app_role_may_rename_the_company_and_change_its_time_zone(
    as_app: Connection,
) -> None:
    """The two settings the API changes, recorded in the history with the user."""
    set_current_user(as_app, 7)
    as_app.execute(
        text("UPDATE core.company SET name = :name, time_zone = :zone"),
        {"name": "Voodoo Mechanical Ltd.", "zone": "America/Toronto"},
    )
    company = as_app.execute(text("SELECT name, time_zone FROM core.company")).one()
    assert tuple(company) == ("Voodoo Mechanical Ltd.", "America/Toronto")
    history = as_app.execute(
        text(
            "SELECT user_id, action, record_type, record_id, before, after FROM core.audit_log"
        )
    ).one()
    assert history[:4] == (7, "update", "core.company", "1")
    assert history.before["name"] == "My Company"
    assert history.after["time_zone"] == "America/Toronto"


@pytest.mark.parametrize(
    "zone", ["UTC", "America/Vancouver", "Asia/Kolkata", "Etc/GMT+8"]
)
def test_real_time_zones_are_accepted(as_app: Connection, zone: str) -> None:
    """Any zone PostgreSQL knows is accepted."""
    as_app.execute(text("UPDATE core.company SET time_zone = :zone"), {"zone": zone})


@pytest.mark.parametrize(
    "zone", ["Mars/Olympus_Mons", "", "america/vancouver", "PST8PDT "]
)
def test_a_time_zone_that_does_not_exist_is_refused(
    as_app: Connection, refusal: Refusal, zone: str
) -> None:
    """Misspelt, empty, or invented zones fail with BW013, not later in a report."""
    statement = "UPDATE core.company SET time_zone = :zone"
    assert refusal(as_app, statement, zone=zone) == "BW013"


@pytest.mark.parametrize("name", ["", "   ", "x" * 201])
def test_an_empty_or_overlong_company_name_is_refused(
    as_app: Connection, refusal: Refusal, name: str
) -> None:
    """A name is 1 to 200 characters and not only spaces."""
    assert refusal(as_app, UPDATE_NAME, name=name) == "23514"


@pytest.mark.parametrize("name", ["x" * 200, "Café Ünïcödé 日本 🙂", "A"])
def test_names_at_the_limits_and_in_any_script_are_accepted(
    as_app: Connection, name: str
) -> None:
    """200 characters, a single character, and Unicode text are fine."""
    as_app.execute(text(UPDATE_NAME), {"name": name})
    assert as_app.execute(text("SELECT name FROM core.company")).scalar_one() == name


@pytest.mark.parametrize(
    ("statement", "sqlstate"),
    [
        (
            "INSERT INTO core.company (name, currency, time_zone) VALUES ('B', 'CAD', 'UTC')",
            "23505",
        ),
        (
            "INSERT INTO core.company (id, name, currency, time_zone) VALUES (2, 'B', 'CAD', 'UTC')",
            "23514",
        ),
        ("UPDATE core.company SET currency = 'cad'", "23514"),
        ("UPDATE core.company SET currency = 'CA'", "23514"),
        ("UPDATE core.company SET currency = 'CADX'", "23514"),
    ],
)
def test_even_the_owner_gets_one_company_with_a_three_letter_currency(
    as_owner: Connection, refusal: Refusal, statement: str, sqlstate: str
) -> None:
    """A second company row, or a malformed currency, is refused for every role."""
    assert refusal(as_owner, statement) == sqlstate
