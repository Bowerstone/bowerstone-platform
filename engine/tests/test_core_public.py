"""What products may use from the core: the company's details, and events."""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from bowerstone_engine.core.public import Company, company, emit_event
from bowerstone_engine.db import APP_ROLE, OWNER_ROLE
from bowerstone_engine.testing import ScratchDatabase


def events(database: ScratchDatabase) -> list[dict[str, Any]]:
    with database.connect(OWNER_ROLE) as conn:
        rows = conn.execute(
            text("SELECT type, source, record_type, record_id, payload FROM core.event")
        )
        return [dict(row) for row in rows.mappings()]


def test_company_reads_the_companys_details(core_db: ScratchDatabase) -> None:
    with core_db.connect(APP_ROLE) as conn:
        assert company(conn) == Company(
            name="My Company", currency="CAD", time_zone="UTC"
        )


def test_emit_event_announces_a_change_in_the_callers_transaction(
    core_db: ScratchDatabase,
) -> None:
    with core_db.connect(APP_ROLE) as conn:
        first = emit_event(
            conn,
            "core.company_renamed",
            source="core",
            record_type="core.company",
            record_id=1,
            payload={"name": "Bowerstone Test Ltd"},
        )
        second = emit_event(
            conn,
            "core.checked",
            source="core",
            record_type="core.company",
            record_id="1",
        )
        conn.commit()

    assert second > first
    assert events(core_db) == [
        {
            "type": "core.company_renamed",
            "source": "core",
            "record_type": "core.company",
            "record_id": "1",
            "payload": {"name": "Bowerstone Test Ltd"},
        },
        {
            "type": "core.checked",
            "source": "core",
            "record_type": "core.company",
            "record_id": "1",
            "payload": {},
        },
    ]


def test_an_event_goes_when_its_transaction_rolls_back(
    core_db: ScratchDatabase,
) -> None:
    with core_db.connect(APP_ROLE) as conn:
        emit_event(
            conn, "core.tried", source="core", record_type="core.company", record_id=1
        )
        conn.rollback()

    assert events(core_db) == []


def test_an_event_whose_type_is_not_its_sources_is_refused(
    core_db: ScratchDatabase,
) -> None:
    with (
        core_db.connect(APP_ROLE) as conn,
        pytest.raises(DBAPIError, match="event_check"),
    ):
        emit_event(
            conn,
            "accounting.entry_posted",
            source="core",
            record_type="core.company",
            record_id=1,
        )
