"""Tests for core.emit_event and core.event: how apps announce a change."""

import json
from collections.abc import Callable

import pytest
from sqlalchemy import Connection, Engine, text

Refusal = Callable[..., str]

EMIT = "SELECT core.emit_event(:type, :source, :record_type, :record_id, CAST(:payload AS jsonb))"
AN_EVENT = {
    "type": "accounting.entry_posted",
    "source": "accounting",
    "record_type": "accounting.journal_entry",
    "record_id": "17",
    "payload": '{"entry_number": 17}',
}


def test_an_app_announces_a_change(as_app: Connection) -> None:
    """emit_event stores the event and returns its id, waiting to be handled."""
    event_id = as_app.execute(text(EMIT), AN_EVENT).scalar_one()
    row = as_app.execute(
        text(
            "SELECT type, source, record_type, record_id, payload, handled_at FROM core.event WHERE id = :id"
        ),
        {"id": event_id},
    ).one()
    assert tuple(row) == (
        "accounting.entry_posted",
        "accounting",
        "accounting.journal_entry",
        "17",
        {"entry_number": 17},
        None,
    )


def test_the_payload_may_be_left_out(as_app: Connection) -> None:
    """An event without details stores an empty object."""
    event_id = as_app.execute(
        text(
            "SELECT core.emit_event('core.company_renamed', 'core', 'core.company', '1')"
        )
    ).scalar_one()
    payload = as_app.execute(
        text("SELECT payload FROM core.event WHERE id = :id"), {"id": event_id}
    ).scalar_one()
    assert payload == {}


@pytest.mark.parametrize(
    "changes",
    [
        {"source": "Accounting"},
        {"source": "a"},
        {"source": None},
        {"type": "accounting"},
        {"type": "invoicing.entry_posted"},
        {"type": "accounting.Entry"},
        {"type": "accounting."},
        {"type": None},
        {"record_type": ""},
        {"record_type": "x" * 101},
        {"record_id": ""},
        {"record_id": None},
        {"payload": "[1, 2]"},
        {"payload": '"text"'},
        {"payload": None},
    ],
)
def test_an_invalid_event_is_refused(
    as_app: Connection, refusal: Refusal, changes: dict[str, str | None]
) -> None:
    """Wrong sources, types, records, or payloads fail with BW014."""
    assert refusal(as_app, EMIT, **{**AN_EVENT, **changes}) == "BW014"


def payload_of_size(
    connection: Connection, character: str, count: int
) -> tuple[str, int]:
    """Build a payload and ask PostgreSQL how many bytes it is as text.

    Args:
        connection: Any connection.
        character: The character to repeat inside the payload.
        count: How many times to repeat it.

    Returns:
        The payload as JSON text, and its size in bytes as PostgreSQL measures it.

    Example:
        >>> payload_of_size(as_app, "a", 10)
        ('{"text": "aaaaaaaaaa"}', 22)
    """
    payload = json.dumps({"text": character * count}, ensure_ascii=False)
    size = connection.execute(
        text("SELECT octet_length(CAST(:payload AS jsonb)::text)"), {"payload": payload}
    ).scalar_one()
    return payload, int(size)


def test_a_payload_of_exactly_64_kb_is_accepted(as_app: Connection) -> None:
    """The limit is inclusive: 65,536 bytes is allowed."""
    payload, size = payload_of_size(as_app, "a", 65_524)
    assert size == 65_536
    as_app.execute(text(EMIT), {**AN_EVENT, "payload": payload})


@pytest.mark.parametrize(("character", "count"), [("a", 65_525), ("€", 21_842)])
def test_a_payload_over_64_kb_is_refused(
    as_app: Connection, refusal: Refusal, character: str, count: int
) -> None:
    """One byte over is refused, counting bytes rather than characters."""
    payload, size = payload_of_size(as_app, character, count)
    assert size > 65_536
    assert refusal(as_app, EMIT, **{**AN_EVENT, "payload": payload}) == "BW014"


def test_an_event_is_kept_only_if_its_change_commits(
    core_pools: dict[str, Engine],
) -> None:
    """An event is part of the transaction that made it, so a rollback removes it."""
    with core_pools["app"].connect() as connection:
        with connection.begin() as transaction:
            event_id = connection.execute(text(EMIT), AN_EVENT).scalar_one()
            transaction.rollback()
        with connection.begin():
            remaining = connection.execute(
                text("SELECT count(*) FROM core.event WHERE id = :id"), {"id": event_id}
            ).scalar_one()
    assert remaining == 0


def test_waiting_events_are_found_through_their_index(as_owner: Connection) -> None:
    """With 200,000 handled events, the waiting ones are read through event_unhandled_idx."""
    as_owner.execute(
        text(
            "INSERT INTO core.event (type, source, record_type, record_id, handled_at) "
            "SELECT 'core.test', 'core', 'core.party', g::text, now() "
            "FROM generate_series(1, 200000) AS g"
        )
    )
    as_owner.execute(text(EMIT), AN_EVENT)
    as_owner.execute(text("ANALYZE core.event"))
    plan = as_owner.execute(
        text(
            "EXPLAIN (ANALYZE, FORMAT TEXT) SELECT * FROM core.event "
            "WHERE handled_at IS NULL ORDER BY id LIMIT 100"
        )
    ).all()
    plan_text = "\n".join(row[0] for row in plan)
    assert "event_unhandled_idx" in plan_text, plan_text
