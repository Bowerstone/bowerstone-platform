"""The API host, with the two example products installed: notes, and tasks, which
needs notes."""

import re
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import SecretStr
from sqlalchemy import Engine, text

from bowerstone_engine.auth import public, require, route_access
from bowerstone_engine.config import Settings, SettingsError
from bowerstone_engine.db import APP_ROLE, OWNER_ROLE
from bowerstone_engine.deps import Db
from bowerstone_engine.installer import install, uninstall
from bowerstone_engine.main import create_app
from bowerstone_engine.migrate import DatabaseCheckError
from bowerstone_engine.products import ProductError, ProductManifest
from bowerstone_engine.testing import MakePackage, ScratchDatabase
from bowerstone_example_notes.manifest import PRODUCT as NOTES
from bowerstone_example_tasks.manifest import PRODUCT as TASKS

CATALOG = {"notes": NOTES, "tasks": TASKS}
TOKEN = "t" * 40


def settings_for(
    database: ScratchDatabase,
    *,
    env: Literal["development", "production"] = "development",
    mode: Literal["server", "desktop"] = "server",
    desktop_token: str | None = None,
    max_body_bytes: int = 1_048_576,
    static_dir: Path | None = None,
) -> Settings:
    url = database.url(APP_ROLE).render_as_string(hide_password=False)
    return Settings(
        env=env,
        mode=mode,
        database_url=SecretStr(url),
        desktop_token=SecretStr(desktop_token) if desktop_token else None,
        max_body_bytes=max_body_bytes,
        static_dir=static_dir,
    )


@pytest.fixture
def installed(database: ScratchDatabase, owner: Engine) -> ScratchDatabase:
    """A database with notes and tasks installed."""
    install(owner, "tasks", CATALOG)
    return database


def serve(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def client(installed: ScratchDatabase) -> Iterator[TestClient]:
    with serve(create_app(settings_for(installed), CATALOG)) as client:
        yield client


def rows(database: ScratchDatabase, sql: str) -> list[dict[str, Any]]:
    with database.connect(OWNER_ROLE) as conn:
        return [dict(row) for row in conn.execute(text(sql)).mappings()]


def error_of(response: Response) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    return body


def faulty_product(make: MakePackage) -> ProductManifest:
    """A test-only product whose route writes a row, then fails unexpectedly."""
    package = make(
        "faulty_migrations",
        {
            "0001_faulty.sql": "CREATE SCHEMA faulty;\n"
            "CREATE TABLE faulty.thing (id int);\n"
            "GRANT USAGE ON SCHEMA faulty TO bowerstone_app;\n"
            "GRANT SELECT, INSERT ON faulty.thing TO bowerstone_app;\n",
            "0001_faulty.down.sql": "DROP SCHEMA faulty CASCADE;\n",
        },
    )

    def build_router() -> APIRouter:
        router = APIRouter()

        @router.post("/boom", dependencies=[public()])
        def boom(db: Db) -> None:
            db.execute(text("INSERT INTO faulty.thing VALUES (1)"))
            raise RuntimeError("secret detail: the password is hunter2")

        return router

    return replace(
        NOTES,
        key="faulty",
        migrations_package=package,
        router=build_router,
        permissions=(),
    )


# Starting up.


def test_production_is_refused_until_sign_in_exists(installed: ScratchDatabase) -> None:
    with pytest.raises(SettingsError, match="no sign-in yet"):
        create_app(settings_for(installed, env="production"), CATALOG)


@pytest.mark.parametrize("token", [None, "short"])
def test_desktop_mode_needs_a_long_token(
    installed: ScratchDatabase, token: str | None
) -> None:
    with pytest.raises(SettingsError, match="BOWERSTONE_DESKTOP_TOKEN"):
        create_app(
            settings_for(installed, mode="desktop", desktop_token=token), CATALOG
        )


def test_the_database_check_runs_before_serving(database: ScratchDatabase) -> None:
    with pytest.raises(DatabaseCheckError, match="core 0001_core"):
        create_app(settings_for(database), CATALOG)


def test_an_enabled_product_whose_package_is_missing_is_refused(
    installed: ScratchDatabase,
) -> None:
    with pytest.raises(
        ProductError, match="notes is installed, but its package is missing"
    ):
        create_app(settings_for(installed), {"tasks": TASKS})


def test_a_missing_static_folder_is_refused(
    installed: ScratchDatabase, tmp_path: Path
) -> None:
    with pytest.raises(SettingsError, match="BOWERSTONE_STATIC_DIR"):
        create_app(settings_for(installed, static_dir=tmp_path / "missing"), CATALOG)


def test_a_removed_products_routes_disappear(
    installed: ScratchDatabase, owner: Engine
) -> None:
    uninstall(owner, "tasks", products=CATALOG)

    with serve(create_app(settings_for(installed), CATALOG)) as client:
        assert (
            client.post("/api/v1/tasks/tasks", json={"title": "x"}).status_code == 404
        )
        assert [p["key"] for p in client.get("/api/v1/products").json()] == ["notes"]


# Permissions on every route.


def test_every_route_declares_what_it_requires(client: TestClient) -> None:
    app = client.app
    assert isinstance(app, FastAPI)
    access = {
        (method, route.path): set(route.access)
        for route in route_access(app)
        for method in route.methods
    }

    assert access == {
        ("GET", "/api/v1/health"): {"public"},
        ("GET", "/api/v1/openapi.json"): {"public"},
        ("GET", "/api/v1/products"): {"core.view"},
        ("GET", "/api/v1/core/company"): {"core.view"},
        ("PATCH", "/api/v1/core/company"): {"core.company"},
        ("GET", "/api/v1/notes/notes"): {"notes.view"},
        ("POST", "/api/v1/notes/notes"): {"notes.edit"},
        ("GET", "/api/v1/notes/notes/{note_id}"): {"notes.view"},
        ("POST", "/api/v1/tasks/tasks"): {"tasks.edit"},
    }


def test_a_route_with_neither_require_nor_public_is_refused(
    database: ScratchDatabase, owner: Engine, migrations_package: MakePackage
) -> None:
    def build_router() -> APIRouter:
        router = APIRouter()

        @router.get("/everything")
        def everything() -> str:
            return "no check at all"

        @router.get("/mixed", dependencies=[public(), require("open.view")])
        def mixed() -> str:
            return "public and not"

        return router

    package = migrations_package(
        "open_migrations",
        {
            "0001_open.sql": "CREATE SCHEMA open;\n",
            "0001_open.down.sql": "DROP SCHEMA open;\n",
        },
    )
    catalog = {
        "open": replace(
            NOTES,
            key="open",
            migrations_package=package,
            router=build_router,
            permissions=(),
        )
    }
    install(owner, "open", catalog)

    with pytest.raises(ProductError) as error:
        create_app(settings_for(database), catalog)
    assert str(error.value) == (
        "Every route must use require() or public(), and these use neither: "
        "GET /api/v1/open/everything, GET /api/v1/open/mixed."
    )


def test_a_user_without_the_permission_is_refused(
    installed: ScratchDatabase, client: TestClient
) -> None:
    with installed.connect(OWNER_ROLE) as conn:
        conn.execute(
            text(
                "DELETE FROM core.role_permission "
                "WHERE role_key = 'owner' AND permission_key = 'core.company'"
            )
        )
        conn.commit()

    response = client.patch("/api/v1/core/company", json={"name": "Refused"})

    assert response.status_code == 403
    assert error_of(response) == {
        "rule": "permission",
        "message": "You need the permission core.company to do this.",
    }
    assert client.get("/api/v1/core/company").status_code == 200


# The desktop token.


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 403),
        ({"X-Bowerstone-Desktop": "wrong"}, 403),
        ({"X-Bowerstone-Desktop": TOKEN[:-1] + "x"}, 403),
        ({"X-Bowerstone-Desktop": TOKEN}, 200),
    ],
    ids=["missing", "wrong", "nearly right", "right"],
)
@pytest.mark.parametrize("path", ["/api/v1/health", "/api/v1/core/company", "/"])
def test_desktop_mode_answers_only_requests_with_the_token(
    installed: ScratchDatabase, headers: dict[str, str], status: int, path: str
) -> None:
    settings = settings_for(installed, mode="desktop", desktop_token=TOKEN)
    with serve(create_app(settings, CATALOG)) as client:
        response = client.get(path, headers=headers)

    assert response.status_code == status
    if status == 403:
        assert error_of(response) == {
            "rule": "desktop_token",
            "message": "This engine answers only the Bowerstone desktop app.",
        }


# Headers, on success and on errors.


def test_every_response_carries_the_security_headers_and_a_request_id(
    installed: ScratchDatabase, migrations_package: MakePackage, owner: Engine
) -> None:
    catalog = {**CATALOG, "faulty": faulty_product(migrations_package)}
    install(owner, "faulty", catalog)
    settings = settings_for(installed, max_body_bytes=100)
    with serve(create_app(settings, catalog)) as client:
        responses = {
            "200 api": client.get("/api/v1/health"),
            "200 page": client.get("/"),
            "404": client.get("/api/v1/notes/notes/999"),
            "405": client.delete("/api/v1/health"),
            "413": client.post("/api/v1/notes/notes", content=b"x" * 101),
            "422": client.post("/api/v1/notes/notes", json={}),
            "500": client.post("/api/v1/faulty/boom"),
        }

    request_ids = set()
    for name, response in responses.items():
        assert response.status_code == int(name[:3]), name
        headers = response.headers
        assert "default-src 'self'" in headers["content-security-policy"], name
        assert headers["x-content-type-options"] == "nosniff", name
        assert headers["x-frame-options"] == "DENY", name
        assert headers["referrer-policy"] == "no-referrer", name
        assert re.fullmatch(r"[0-9a-f]{32}", headers["x-request-id"]), name
        request_ids.add(headers["x-request-id"])
    assert len(request_ids) == len(responses)
    assert responses["200 api"].headers["cache-control"] == "no-store"
    assert responses["405"].headers["allow"] == "GET"


# The error shape, for each status.


def test_a_broken_rule_is_422_with_its_rule(client: TestClient) -> None:
    response = client.post(
        "/api/v1/tasks/tasks", json={"title": "Call", "note_id": 999}
    )

    assert response.status_code == 422
    assert error_of(response) == {
        "rule": "tasks.note_exists",
        "message": "There is no note 999 for this task to point at.",
    }


def test_a_missing_record_is_404(client: TestClient) -> None:
    response = client.get("/api/v1/notes/notes/999")

    assert response.status_code == 404
    assert error_of(response) == {
        "rule": "not_found",
        "message": "There is no note 999.",
    }


def test_input_errors_list_each_field(client: TestClient) -> None:
    response = client.post(
        "/api/v1/tasks/tasks", json={"title": "", "cost": "ten", "colour": "red"}
    )

    assert response.status_code == 422
    body = error_of(response)
    assert (body["rule"], body["message"]) == (
        "input",
        "Some of the input cannot be used.",
    )
    assert [field["field"] for field in body["fields"]] == [
        "body.title",
        "body.cost",
        "body.colour",
    ]


def test_an_unknown_api_address_is_404_and_a_wrong_method_is_405(
    client: TestClient,
) -> None:
    assert error_of(client.get("/api/v1/nowhere")) == {
        "rule": "not_found",
        "message": "There is nothing at this address.",
    }
    response = client.put("/api/v1/notes/notes")
    assert response.status_code == 405
    assert error_of(response)["rule"] == "method_not_allowed"


def test_an_unexpected_error_is_500_with_no_details_and_keeps_nothing(
    installed: ScratchDatabase, owner: Engine, migrations_package: MakePackage
) -> None:
    catalog = {**CATALOG, "faulty": faulty_product(migrations_package)}
    install(owner, "faulty", catalog)

    with serve(create_app(settings_for(installed), catalog)) as client:
        response = client.post("/api/v1/faulty/boom")

    assert response.status_code == 500
    assert error_of(response) == {
        "rule": "unexpected",
        "message": "Something went wrong on the server.",
    }
    assert "hunter2" not in response.text
    # The route's write was rolled back with the failed request.
    assert rows(installed, "SELECT count(*) AS n FROM faulty.thing") == [{"n": 0}]


# The body limit.


def test_a_body_over_the_limit_is_refused_by_its_length(
    installed: ScratchDatabase,
) -> None:
    with serve(
        create_app(settings_for(installed, max_body_bytes=100), CATALOG)
    ) as client:
        response = client.post("/api/v1/notes/notes", json={"body": "x" * 200})
        assert (
            client.post("/api/v1/notes/notes", json={"body": "fits"}).status_code == 201
        )

    assert response.status_code == 413
    assert error_of(response) == {
        "rule": "too_large",
        "message": "The request is larger than the engine accepts.",
    }
    assert rows(installed, "SELECT body FROM notes.note") == [{"body": "fits"}]


def test_a_streamed_body_over_the_limit_is_refused_while_it_arrives(
    installed: ScratchDatabase,
) -> None:
    def chunks() -> Iterator[bytes]:
        yield b'{"body": "'
        for _ in range(50):
            yield b"x" * 10
        yield b'"}'

    with serve(
        create_app(settings_for(installed, max_body_bytes=100), CATALOG)
    ) as client:
        response = client.post(
            "/api/v1/notes/notes",
            content=chunks(),
            headers={"content-type": "application/json"},
        )

    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    assert rows(installed, "SELECT count(*) AS n FROM notes.note") == [{"n": 0}]


# Money.


@pytest.mark.parametrize("cost", [10.5, 10, True])
def test_an_amount_that_is_not_a_string_is_refused(
    client: TestClient, cost: object
) -> None:
    response = client.post("/api/v1/tasks/tasks", json={"title": "Paint", "cost": cost})

    assert response.status_code == 422
    assert error_of(response)["fields"] == [
        {
            "field": "body.cost",
            "message": 'amounts must be sent as strings, such as "10.50"',
        }
    ]


def test_an_amount_is_stored_and_sent_back_exactly(client: TestClient) -> None:
    response = client.post(
        "/api/v1/tasks/tasks", json={"title": "Paint", "cost": "10.50"}
    )

    assert response.status_code == 201
    assert response.json()["cost"] == "10.5000"


# Pages.


def test_a_list_pages_through_with_next_after(client: TestClient) -> None:
    ids = [
        client.post("/api/v1/notes/notes", json={"body": f"Note {n}"}).json()["id"]
        for n in range(5)
    ]

    first = client.get("/api/v1/notes/notes", params={"limit": 3}).json()
    assert [note["id"] for note in first["items"]] == ids[:3]
    assert first["next_after"] == str(ids[2])

    second = client.get(
        "/api/v1/notes/notes", params={"limit": 3, "after": first["next_after"]}
    ).json()
    assert [note["id"] for note in second["items"]] == ids[3:]
    assert second["next_after"] is None


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"limit": 201}, "query.limit"),
        ({"limit": 0}, "query.limit"),
        ({"after": "x"}, "query.after"),
    ],
)
def test_a_page_request_out_of_bounds_is_refused(
    client: TestClient, params: dict[str, int | str], field: str
) -> None:
    response = client.get("/api/v1/notes/notes", params=params)

    assert response.status_code == 422
    assert [f["field"] for f in error_of(response)["fields"]] == [field]


# The shell.


def test_the_placeholder_page_is_served_until_the_shell_exists(
    client: TestClient,
) -> None:
    for path in ("/", "/accounting/entries", "/any/page"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "The engine is running." in response.text, path


def test_unknown_pages_fall_back_to_the_shell_but_missing_files_do_not(
    installed: ScratchDatabase, tmp_path: Path
) -> None:
    (tmp_path / "index.html").write_text("<p>The shell</p>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "shell.js").write_text("export {};")

    with serve(
        create_app(settings_for(installed, static_dir=tmp_path), CATALOG)
    ) as client:
        assert client.get("/assets/shell.js").text == "export {};"
        assert client.get("/notes/list").text == "<p>The shell</p>"
        missing = client.get("/assets/missing.js")

    assert missing.status_code == 404
    assert error_of(missing)["rule"] == "not_found"


# The core's routes.


def test_health_is_public(client: TestClient) -> None:
    assert client.get("/api/v1/health").json() == {"status": "ok"}


def test_the_installed_products_are_listed(client: TestClient) -> None:
    assert client.get("/api/v1/products").json() == [
        {
            "key": "notes",
            "name": "Notes",
            "version": "0.1.0",
            "requires": [],
            "ui_versions": ">=0.0,<0.1",
            "screens": None,
        },
        {
            "key": "tasks",
            "name": "Tasks",
            "version": "0.1.0",
            "requires": ["notes"],
            "ui_versions": ">=0.0,<0.1",
            "screens": None,
        },
    ]


def test_the_company_details_change_and_the_change_is_in_the_history(
    installed: ScratchDatabase, client: TestClient
) -> None:
    assert client.get("/api/v1/core/company").json() == {
        "name": "My Company",
        "currency": "CAD",
        "time_zone": "UTC",
    }

    response = client.patch(
        "/api/v1/core/company",
        json={"name": "Bowerstone Test Ltd", "time_zone": "America/Toronto"},
    )

    assert response.json() == {
        "name": "Bowerstone Test Ltd",
        "currency": "CAD",
        "time_zone": "America/Toronto",
    }
    [entry] = rows(
        installed,
        "SELECT user_id, record_type FROM core.audit_log "
        "WHERE record_type = 'core.company'",
    )
    # The built-in local user has no row in core.app_user, so no user is recorded yet.
    assert entry == {"user_id": None, "record_type": "core.company"}


def test_a_change_that_changes_nothing_writes_nothing(
    installed: ScratchDatabase, client: TestClient
) -> None:
    assert client.patch("/api/v1/core/company", json={}).status_code == 200
    assert rows(
        installed,
        "SELECT count(*) AS n FROM core.audit_log WHERE record_type = 'core.company'",
    ) == [{"n": 0}]


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"time_zone": "Mars/Olympus"}, "body.time_zone"),
        ({"currency": "cad"}, "body.currency"),
        ({"name": "   "}, "body.name"),
        ({"colour": "red"}, "body.colour"),
    ],
)
def test_wrong_company_details_are_refused(
    client: TestClient, changes: dict[str, str], field: str
) -> None:
    response = client.patch("/api/v1/core/company", json=changes)

    assert response.status_code == 422
    assert [f["field"] for f in error_of(response)["fields"]] == [field]


def test_the_api_schema_is_served_with_money_as_a_string(client: TestClient) -> None:
    schema = client.get("/api/v1/openapi.json").json()

    assert "/api/v1/notes/notes" in schema["paths"]
    cost = schema["components"]["schemas"]["Task"]["properties"]["cost"]
    assert {"type": "string", "pattern": r"^-?\d{1,15}(\.\d{1,4})?$"}.items() <= next(
        option for option in cost["anyOf"] if option.get("type") == "string"
    ).items()
