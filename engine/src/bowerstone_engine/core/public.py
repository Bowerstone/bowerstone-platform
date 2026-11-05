"""What products may use from the core: the company's details, and events.

Products may import this module. It is part of the engine's SDK.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import Connection, text


@dataclass(frozen=True)
class Company:
    """The one company this installation keeps books for."""

    name: str
    # An ISO 4217 code, such as CAD.
    currency: str
    # A time zone name, such as America/Toronto.
    time_zone: str


def company(conn: Connection) -> Company:
    """The company's details."""
    row = conn.execute(text("SELECT name, currency, time_zone FROM core.company")).one()
    return Company(name=row.name, currency=row.currency, time_zone=row.time_zone)


def emit_event(
    conn: Connection,
    event_type: str,
    *,
    source: str,
    record_type: str,
    record_id: str | int,
    payload: Mapping[str, object] | None = None,
) -> int:
    """Announces a change, in the caller's transaction, and returns the event's id.

    `event_type` starts with `source`, the product's key, as in
    accounting.entry_posted. `record_type` names the table, as schema.table.
    `payload` must turn into JSON. If the transaction rolls back, the event
    goes with it.
    """
    event_id: int = conn.execute(
        text(
            "SELECT core.emit_event(:event_type, :source, :record_type, :record_id, "
            "CAST(:payload AS jsonb))"
        ),
        {
            "event_type": event_type,
            "source": source,
            "record_type": record_type,
            "record_id": str(record_id),
            "payload": json.dumps(dict(payload or {})),
        },
    ).scalar_one()
    return event_id
