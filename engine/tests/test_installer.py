"""Installing, updating, and removing products, with the two example products.

notes has deletable data. tasks needs notes, and its data may never be deleted.
"""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text

from bowerstone_engine.db import APP_ROLE, OWNER_ROLE
from bowerstone_engine.installer import (
    InstalledProduct,
    dependency_order,
    install,
    installed_products,
    uninstall,
    upgrade_all,
)
from bowerstone_engine.migrate import DatabaseCheckError
from bowerstone_engine.products import Permission, ProductError
from bowerstone_engine.testing import MakePackage, ScratchDatabase
from bowerstone_example_notes.manifest import PRODUCT as NOTES
from bowerstone_example_tasks.manifest import PRODUCT as TASKS

CATALOG = {"notes": NOTES, "tasks": TASKS}
NOTES_MIGRATIONS = Path(__file__).parents[2] / (
    "tests/example_products/notes/src/bowerstone_example_notes/migrations"
)


def refusal(action: Callable[[], object]) -> str:
    with pytest.raises(ProductError) as error:
        action()
    return str(error.value)


def query(
    database: ScratchDatabase, sql: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    with database.connect(OWNER_ROLE) as conn:
        return [dict(row) for row in conn.execute(text(sql), params or {}).mappings()]


def installed(database: ScratchDatabase) -> dict[str, InstalledProduct]:
    with database.connect(OWNER_ROLE) as conn:
        return installed_products(conn)


def schemas(database: ScratchDatabase) -> set[str]:
    rows = query(
        database,
        "SELECT nspname::text AS name FROM pg_namespace "
        "WHERE nspname IN ('notes', 'tasks')",
    )
    return {row["name"] for row in rows}


def applied(database: ScratchDatabase) -> list[str]:
    rows = query(
        database,
        "SELECT module || ' ' || version AS migration FROM core.applied_migration "
        "ORDER BY applied_at, module",
    )
    return [row["migration"] for row in rows]


def permissions(database: ScratchDatabase, module: str) -> dict[str, list[str]]:
    rows = query(
        database,
        "SELECT p.key, array_remove(array_agg(rp.role_key ORDER BY rp.role_key), NULL) "
        "AS roles FROM core.permission AS p "
        "LEFT JOIN core.role_permission AS rp ON rp.permission_key = p.key "
        "WHERE p.module = :module GROUP BY p.key ORDER BY p.key",
        {"module": module},
    )
    return {row["key"]: row["roles"] for row in rows}


def add_note(database: ScratchDatabase, body: str) -> None:
    with database.connect(APP_ROLE) as conn:
        conn.execute(
            text("INSERT INTO notes.note (body) VALUES (:body)"), {"body": body}
        )
        conn.commit()


def notes_in(database: ScratchDatabase) -> list[str]:
    return [row["body"] for row in query(database, "SELECT body FROM notes.note")]


# Installing.


def test_installing_a_product_installs_what_it_needs_first(
    database: ScratchDatabase, owner: Engine
) -> None:
    # No catalog given, so the installer finds the products through their entry points.
    assert install(owner, "tasks") == ["notes", "tasks"]

    assert applied(database) == ["core 1", "notes 1", "tasks 1", "tasks 2"]
    assert schemas(database) == {"notes", "tasks"}
    assert installed(database) == {
        "notes": InstalledProduct("notes", "0.1.0", (), True),
        "tasks": InstalledProduct("tasks", "0.1.0", ("notes",), True),
    }
    assert permissions(database, "notes") == {
        "notes.edit": ["owner", "staff"],
        "notes.view": ["owner", "read_only", "staff"],
    }
    assert permissions(database, "tasks") == {
        "tasks.edit": ["owner", "staff"],
        "tasks.view": ["owner", "read_only", "staff"],
    }


def test_installing_again_changes_nothing(
    database: ScratchDatabase, owner: Engine
) -> None:
    install(owner, "tasks", CATALOG)
    history = query(database, "SELECT count(*) AS n FROM core.audit_log")

    assert install(owner, "tasks", CATALOG) == []
    assert install(owner, "notes", CATALOG) == []
    assert query(database, "SELECT count(*) AS n FROM core.audit_log") == history


def test_dependency_order_puts_each_product_after_what_it_needs() -> None:
    assert dependency_order(["tasks"], CATALOG) == ["notes", "tasks"]
    assert dependency_order(["notes", "tasks"], CATALOG) == ["notes", "tasks"]
    assert dependency_order(["tasks", "notes"], CATALOG) == ["notes", "tasks"]


def test_a_missing_dependency_is_refused_before_anything_runs(
    database: ScratchDatabase, owner: Engine
) -> None:
    assert refusal(lambda: install(owner, "tasks", {"tasks": TASKS})) == (
        "The product tasks needs notes, whose package is not installed."
    )
    assert query(database, "SELECT to_regclass('core.installed_product') AS t") == [
        {"t": None}
    ]


def test_a_product_that_is_not_present_is_refused(owner: Engine) -> None:
    assert refusal(lambda: install(owner, "invoices", CATALOG)) == (
        "The product invoices's package is not installed."
    )


def test_a_loop_of_dependencies_is_refused(owner: Engine) -> None:
    catalog = {
        "alpha": replace(NOTES, key="alpha", requires=("beta",), permissions=()),
        "beta": replace(NOTES, key="beta", requires=("gamma",), permissions=()),
        "gamma": replace(NOTES, key="gamma", requires=("alpha",), permissions=()),
    }

    assert refusal(lambda: install(owner, "alpha", catalog)) == (
        "These products need each other in a loop: alpha, beta, gamma, alpha."
    )


def test_an_engine_version_outside_the_range_is_refused(
    database: ScratchDatabase, owner: Engine
) -> None:
    catalog = {"notes": replace(NOTES, engine_versions=">=9,<10")}

    assert refusal(lambda: install(owner, "notes", catalog)) == (
        "The product notes works with engine versions >=9,<10, "
        "and this engine is 0.0.0."
    )
    assert schemas(database) == set()
    assert installed(database) == {}


@pytest.mark.parametrize("key", ["Notes", "n", "core", "public", "pg_notes"])
def test_a_bad_key_is_refused(
    database: ScratchDatabase, owner: Engine, key: str
) -> None:
    catalog = {key: replace(NOTES, key=key, permissions=())}

    assert "The product key" in refusal(lambda: install(owner, key, catalog))
    assert installed(database) == {}


def test_a_first_migration_must_create_the_products_schema(
    database: ScratchDatabase,
    owner: Engine,
    migrations_package: MakePackage,
) -> None:
    package = migrations_package(
        "schemaless_migrations",
        {
            "0001_start.sql": "CREATE TABLE public.schemaless (id int);\n",
            "0001_start.down.sql": "DROP TABLE public.schemaless;\n",
        },
    )
    catalog = {
        "schemaless": replace(
            NOTES, key="schemaless", migrations_package=package, permissions=()
        )
    }

    assert refusal(lambda: install(owner, "schemaless", catalog)) == (
        "The product schemaless's first migration must create the schema schemaless."
    )
    assert installed(database) == {}


def test_a_permission_for_a_role_that_does_not_exist_is_refused(
    database: ScratchDatabase, owner: Engine
) -> None:
    catalog = {
        "notes": replace(
            NOTES, permissions=(Permission("notes.view", "View notes", ("auditor",)),)
        )
    }

    assert refusal(lambda: install(owner, "notes", catalog)) == (
        "The product notes's permissions name roles that do not exist: auditor."
    )
    assert installed(database) == {}
    assert permissions(database, "notes") == {}


# Removing.


@pytest.mark.parametrize("delete_data", [False, True])
def test_removing_a_product_another_needs_is_refused(
    database: ScratchDatabase, owner: Engine, delete_data: bool
) -> None:
    install(owner, "tasks", CATALOG)

    assert refusal(
        lambda: uninstall(owner, "notes", delete_data=delete_data, products=CATALOG)
    ) == ("The product notes cannot be removed while tasks needs it. Remove it first.")
    assert all(product.is_enabled for product in installed(database).values())


def test_removing_a_product_that_is_not_installed_is_refused(owner: Engine) -> None:
    assert refusal(lambda: uninstall(owner, "notes", products=CATALOG)) == (
        "The product notes is not installed."
    )


def test_a_removed_product_keeps_its_data_and_comes_back_with_it(
    database: ScratchDatabase, owner: Engine
) -> None:
    install(owner, "notes", CATALOG)
    add_note(database, "Kept while removed")

    uninstall(owner, "notes", products=CATALOG)

    assert installed(database)["notes"].is_enabled is False
    assert notes_in(database) == ["Kept while removed"]
    assert "notes.edit" in permissions(database, "notes")
    # A removed product needs no package, so updating works without it.
    upgrade_all(owner, products={})

    assert install(owner, "notes", CATALOG) == ["notes"]
    assert installed(database)["notes"].is_enabled is True
    assert notes_in(database) == ["Kept while removed"]


def test_deleting_a_products_data_removes_it_completely(
    database: ScratchDatabase, owner: Engine
) -> None:
    install(owner, "notes", CATALOG)
    add_note(database, "Deleted with the product")

    uninstall(owner, "notes", delete_data=True, products=CATALOG)

    assert schemas(database) == set()
    assert installed(database) == {}
    assert permissions(database, "notes") == {}
    assert applied(database) == ["core 1"]

    # Installing it again starts it fresh.
    assert install(owner, "notes", CATALOG) == ["notes"]
    assert notes_in(database) == []


def test_data_that_may_never_be_deleted_is_kept(
    database: ScratchDatabase, owner: Engine
) -> None:
    install(owner, "tasks", CATALOG)
    uninstall(owner, "tasks", products=CATALOG)

    assert refusal(
        lambda: uninstall(owner, "tasks", delete_data=True, products=CATALOG)
    ) == ("The data of the product tasks may never be deleted.")
    assert schemas(database) == {"notes", "tasks"}
    assert "tasks" in installed(database)


# Updating everything.


def test_upgrade_all_migrates_only_the_core_when_nothing_is_installed(
    database: ScratchDatabase, owner: Engine
) -> None:
    assert [str(m) for m in upgrade_all(owner, CATALOG)] == ["core 0001_core"]
    assert upgrade_all(owner, CATALOG) == []


def test_upgrade_all_applies_new_migrations_and_brings_permissions_up_to_date(
    database: ScratchDatabase,
    owner: Engine,
    migrations_package: MakePackage,
) -> None:
    install(owner, "notes", CATALOG)
    # The owner takes notes.view away from read-only. An update keeps that choice.
    with database.connect(OWNER_ROLE) as conn:
        conn.execute(
            text(
                "DELETE FROM core.role_permission "
                "WHERE role_key = 'read_only' AND permission_key = 'notes.view'"
            )
        )
        conn.commit()

    # Version 0.2.0 of notes: its released migration unchanged, plus a new one.
    files = {path.name: path.read_text() for path in NOTES_MIGRATIONS.glob("*.sql")}
    files["0002_pinned.sql"] = "ALTER TABLE notes.note ADD COLUMN pinned boolean;\n"
    files["0002_pinned.down.sql"] = "ALTER TABLE notes.note DROP COLUMN pinned;\n"
    newer = replace(
        NOTES,
        version="0.2.0",
        migrations_package=migrations_package("notes_v2", files),
        permissions=(
            Permission("notes.view", "Read notes", ("owner", "staff", "read_only")),
            Permission("notes.pin", "Pin notes", ("owner",)),
        ),
    )

    assert [str(m) for m in upgrade_all(owner, {"notes": newer})] == [
        "notes 0002_pinned"
    ]

    assert installed(database)["notes"].version == "0.2.0"
    assert permissions(database, "notes") == {
        "notes.pin": ["owner"],
        "notes.view": ["owner", "staff"],
    }
    assert query(
        database, "SELECT description FROM core.permission WHERE key = 'notes.view'"
    ) == [{"description": "Read notes"}]


def test_upgrade_all_stops_when_an_enabled_products_package_is_missing(
    owner: Engine,
) -> None:
    install(owner, "notes", CATALOG)

    assert refusal(lambda: upgrade_all(owner, {"tasks": TASKS})) == (
        "The product notes is installed, but its package is missing."
    )


def test_upgrade_all_refuses_an_enabled_product_whose_needs_are_not_installed(
    database: ScratchDatabase, owner: Engine
) -> None:
    install(owner, "tasks", CATALOG)
    with database.connect(OWNER_ROLE) as conn:
        conn.execute(
            text(
                "UPDATE core.installed_product SET is_enabled = false "
                "WHERE key = 'notes'"
            )
        )
        conn.commit()

    assert refusal(lambda: upgrade_all(owner, CATALOG)) == (
        "The product tasks needs notes, which is not installed. "
        "Install it with: bowerstone products install notes"
    )


def test_every_operation_runs_the_database_check_first(
    database: ScratchDatabase,
) -> None:
    app = database.engine(APP_ROLE)
    try:
        for action in (
            lambda: upgrade_all(app, CATALOG),
            lambda: install(app, "notes", CATALOG),
            lambda: uninstall(app, "notes", products=CATALOG),
        ):
            with pytest.raises(DatabaseCheckError, match="migrations must run as"):
                action()
    finally:
        app.dispose()
