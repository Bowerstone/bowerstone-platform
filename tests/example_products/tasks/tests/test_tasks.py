"""The tasks product's own tests, written with the engine's testing kit.

tasks needs notes, so the kit installs notes first, and both products' routes
are served.
"""

from fastapi.testclient import TestClient
from sqlalchemy import Connection, text

from bowerstone_engine.installer import installed_products


def test_the_kit_installs_tasks_and_what_it_needs(db: Connection) -> None:
    installed = installed_products(db)

    assert sorted(installed) == ["notes", "tasks"]
    assert installed["tasks"].requires == ("notes",)
    assert db.execute(
        text(
            "SELECT array_agg(module || ' ' || version ORDER BY module, version) "
            "FROM core.applied_migration"
        )
    ).scalar_one() == ["core 1", "notes 1", "tasks 1", "tasks 2"]


def test_a_task_can_point_at_a_note(api: TestClient) -> None:
    note = api.post("/api/v1/notes/notes", json={"body": "Quote from the painter"})

    response = api.post(
        "/api/v1/tasks/tasks",
        json={
            "title": "Accept the quote",
            "note_id": note.json()["id"],
            "cost": "1250.00",
        },
    )

    assert response.status_code == 201
    assert response.json() == {
        "id": response.json()["id"],
        "title": "Accept the quote",
        "note_id": note.json()["id"],
        "cost": "1250.0000",
        "done": False,
    }


def test_a_task_pointing_at_a_missing_note_is_refused(
    api: TestClient, db: Connection
) -> None:
    response = api.post(
        "/api/v1/tasks/tasks", json={"title": "Orphan", "note_id": 999999}
    )

    assert response.status_code == 422
    assert response.json()["rule"] == "tasks.note_exists"
    assert db.execute(text("SELECT count(*) FROM tasks.task")).scalar_one() == 0


def test_a_cost_must_be_sent_as_a_string(api: TestClient) -> None:
    response = api.post("/api/v1/tasks/tasks", json={"title": "Paint", "cost": 12.5})

    assert response.status_code == 422
    assert response.json()["fields"][0]["field"] == "body.cost"
