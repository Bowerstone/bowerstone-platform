"""The notes product's own tests, written with the engine's testing kit.

The kit gives `db`, a connection rolled back after each test, `api`, whose
requests share that connection, and `committed_database`, for tests that need
real commits.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, text
from sqlalchemy.exc import OperationalError

from bowerstone_engine.db import APP_ROLE
from bowerstone_engine.installer import installed_products
from bowerstone_engine.testing import ScratchDatabase
from bowerstone_example_notes.public import note_exists


def note_count(db: Connection) -> int:
    count: int = db.execute(text("SELECT count(*) FROM notes.note")).scalar_one()
    return count


def test_the_kit_installs_notes(db: Connection) -> None:
    installed = installed_products(db)

    assert list(installed) == ["notes"]
    assert installed["notes"].is_enabled
    assert db.execute(text("SELECT current_user")).scalar_one() == APP_ROLE


def test_a_note_added_through_the_api_is_in_the_database(
    api: TestClient, db: Connection
) -> None:
    response = api.post("/api/v1/notes/notes", json={"body": "Call the bank"})

    assert response.status_code == 201
    note_id = response.json()["id"]
    assert api.get(f"/api/v1/notes/notes/{note_id}").json() == {
        "id": note_id,
        "body": "Call the bank",
    }
    # The request ran on the test's own connection, so the test sees its write.
    assert note_exists(db, note_id)


# The next two tests each start with no notes, and add one. Both pass only
# because the kit rolls each test back.


def test_each_test_starts_clean(api: TestClient, db: Connection) -> None:
    assert note_count(db) == 0
    api.post("/api/v1/notes/notes", json={"body": "First test's note"})
    assert note_count(db) == 1


def test_each_test_starts_clean_again(api: TestClient, db: Connection) -> None:
    assert note_count(db) == 0
    api.post("/api/v1/notes/notes", json={"body": "Second test's note"})
    assert note_count(db) == 1


def test_a_failed_request_undoes_only_itself(api: TestClient, db: Connection) -> None:
    api.post("/api/v1/notes/notes", json={"body": "Kept"})

    assert api.post("/api/v1/notes/notes", json={"body": ""}).status_code == 422
    assert api.get("/api/v1/notes/notes/999999").status_code == 404
    assert note_count(db) == 1


def test_notes_page_through(api: TestClient) -> None:
    for number in range(3):
        api.post("/api/v1/notes/notes", json={"body": f"Note {number}"})

    first = api.get("/api/v1/notes/notes", params={"limit": 2}).json()
    second = api.get(
        "/api/v1/notes/notes", params={"limit": 2, "after": first["next_after"]}
    ).json()

    assert [note["body"] for note in first["items"] + second["items"]] == [
        "Note 0",
        "Note 1",
        "Note 2",
    ]
    assert second["next_after"] is None


def test_adding_a_note_announces_it(api: TestClient, db: Connection) -> None:
    note_id = api.post("/api/v1/notes/notes", json={"body": "Announced"}).json()["id"]

    assert db.execute(
        text("SELECT type, record_id FROM core.event WHERE source = 'notes'")
    ).all() == [("notes.note_added", str(note_id))]


def test_a_second_connection_waits_for_the_first_to_commit(
    committed_database: ScratchDatabase,
) -> None:
    # A race test needs two connections that really commit, so it uses a
    # database of its own instead of `db`.
    engine = committed_database.engine(APP_ROLE)
    try:
        with engine.connect() as first, engine.connect() as second:
            note_id = first.execute(
                text("INSERT INTO notes.note (body) VALUES ('Draft') RETURNING id")
            ).scalar_one()
            first.commit()

            first.execute(
                text("UPDATE notes.note SET body = 'Edited' WHERE id = :id"),
                {"id": note_id},
            )
            # The first connection holds the row's lock, so the second must wait.
            second.execute(text("SET lock_timeout = '200ms'"))
            with pytest.raises(OperationalError, match="lock timeout"):
                second.execute(
                    text("UPDATE notes.note SET body = 'Clash' WHERE id = :id"),
                    {"id": note_id},
                )
            second.rollback()

            first.commit()
            assert second.execute(
                text("SELECT body FROM notes.note WHERE id = :id"), {"id": note_id}
            ).scalar_one() == ("Edited")
    finally:
        engine.dispose()
