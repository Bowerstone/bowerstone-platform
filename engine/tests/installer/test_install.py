"""Tests for bowerstone_engine.installer.install against PostgreSQL 18."""

import re
import threading
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, Row

from bowerstone_engine.apps import AppManifest, Permission
from bowerstone_engine.errors import InstallError
from bowerstone_engine.installer import install

ENGINE_VERSION = "0.0.5"
MakeApp = Callable[..., AppManifest]
Query = Callable[..., Sequence[Row[Any]]]
Schemas = Callable[[], set[str]]
Installed = Callable[[], dict[str, tuple[str, bool]]]


def catalog(*manifests: AppManifest) -> dict[str, AppManifest]:
    """Make the available-apps mapping the installer takes.

    Args:
        manifests: The apps whose packages are installed.

    Returns:
        The manifests, by key.

    Example:
        >>> sorted(catalog(ledger, billing))
        ['billing', 'ledger']
    """
    return {manifest.key: manifest for manifest in manifests}


def test_installing_an_app_installs_what_it_needs_first(
    connection: Connection,
    ledger: AppManifest,
    billing: AppManifest,
    schemas: Schemas,
    installed: Installed,
) -> None:
    """Core, then ledger, then billing: each migration runs once, in dependency order."""
    applied = install(connection, "billing", catalog(ledger, billing), ENGINE_VERSION)
    assert [(item.module, item.migration.version) for item in applied] == [
        ("core", 1),
        ("ledger", 1),
        ("billing", 1),
    ]
    assert schemas() == {"core", "ledger", "billing"}
    assert installed() == {"ledger": ("0.1.0", True), "billing": ("0.1.0", True)}


def test_an_installed_app_records_what_it_needs(
    connection: Connection, ledger: AppManifest, billing: AppManifest, query: Query
) -> None:
    """core.installed_app keeps each app's requires, for the removal checks."""
    install(connection, "billing", catalog(ledger, billing), ENGINE_VERSION)
    rows = query("SELECT key, requires FROM core.installed_app ORDER BY key")
    assert [(row.key, row.requires) for row in rows] == [
        ("billing", ["ledger"]),
        ("ledger", []),
    ]


def test_an_apps_permissions_are_registered_with_their_default_roles(
    connection: Connection, ledger: AppManifest, query: Query
) -> None:
    """Each declared permission is added under the app and given to its default roles."""
    install(connection, "ledger", catalog(ledger), ENGINE_VERSION)
    permissions = query(
        "SELECT key, app_key, description FROM core.permission WHERE app_key = 'ledger'"
    )
    assert {tuple(row) for row in permissions} == {
        ("ledger.view", "ledger", "View the ledger"),
        ("ledger.edit", "ledger", "Change the ledger"),
    }
    held = query(
        "SELECT role_key, permission_key FROM core.role_permission WHERE permission_key LIKE 'ledger.%'"
    )
    assert {tuple(row) for row in held} == {
        ("owner", "ledger.view"),
        ("staff", "ledger.view"),
        ("read_only", "ledger.view"),
        ("owner", "ledger.edit"),
    }


def test_installing_again_changes_nothing(
    connection: Connection, ledger: AppManifest, installed: Installed
) -> None:
    """A second install of the same version applies nothing and leaves the record."""
    install(connection, "ledger", catalog(ledger), ENGINE_VERSION)
    assert install(connection, "ledger", catalog(ledger), ENGINE_VERSION) == []
    assert installed() == {"ledger": ("0.1.0", True)}


def test_a_missing_dependency_is_refused_before_anything_runs(
    connection: Connection, billing: AppManifest, schemas: Schemas
) -> None:
    """billing needs ledger, which no package provides, so the database is untouched."""
    with pytest.raises(InstallError, match="billing needs ledger"):
        install(connection, "billing", catalog(billing), ENGINE_VERSION)
    assert schemas() == set()


def test_a_dependency_loop_is_refused_before_anything_runs(
    connection: Connection, app_factory: MakeApp, schemas: Schemas
) -> None:
    """Two apps that need each other are refused, and nothing is installed."""
    ledger = app_factory("ledger", requires=("billing",))
    billing = app_factory("billing", requires=("ledger",))
    with pytest.raises(InstallError, match=re.escape("billing -> ledger -> billing")):
        install(connection, "billing", catalog(ledger, billing), ENGINE_VERSION)
    assert schemas() == set()


def test_an_engine_version_outside_the_range_is_refused_before_anything_runs(
    connection: Connection, app_factory: MakeApp, billing: AppManifest, schemas: Schemas
) -> None:
    """A dependency that does not support this engine stops the whole install."""
    ledger = app_factory("ledger", engine_versions=">=0.1,<0.2")
    with pytest.raises(
        InstallError,
        match=re.escape(
            "works with engine versions >=0.1,<0.2, not this engine's 0.0.5"
        ),
    ):
        install(connection, "billing", catalog(ledger, billing), ENGINE_VERSION)
    assert schemas() == set()


def test_an_unknown_app_is_refused(connection: Connection, ledger: AppManifest) -> None:
    """Installing an app that no package provides fails, naming it."""
    with pytest.raises(InstallError, match="payroll: no installed package provides it"):
        install(connection, "payroll", catalog(ledger), ENGINE_VERSION)


def test_an_invalid_engine_version_is_refused(
    connection: Connection, ledger: AppManifest
) -> None:
    """The engine's own version must be a real version number."""
    with pytest.raises(InstallError, match="is not a valid engine version"):
        install(connection, "ledger", catalog(ledger), "zero")


def test_an_app_whose_first_migration_skips_its_schema_is_refused(
    connection: Connection, app_factory: MakeApp, installed: Installed
) -> None:
    """The first migration must create a schema named after the key; otherwise it is not recorded."""
    misnamed = app_factory(
        "ledger",
        migrations={
            "0001_ledger.sql": "CREATE SCHEMA wrong_name;\n",
            "0001_ledger.down.sql": "DROP SCHEMA wrong_name;\n",
        },
    )
    with pytest.raises(InstallError, match="must create the schema ledger"):
        install(connection, "ledger", catalog(misnamed), ENGINE_VERSION)
    assert installed() == {}


def test_a_permission_for_a_role_that_does_not_exist_is_refused(
    connection: Connection, app_factory: MakeApp, installed: Installed
) -> None:
    """A default role must be one of the roles in core.role."""
    strange = app_factory(
        "ledger",
        permissions=(Permission("ledger.audit", "Audit the ledger", ("auditor",)),),
    )
    with pytest.raises(
        InstallError, match="names the role auditor, which does not exist"
    ):
        install(connection, "ledger", catalog(strange), ENGINE_VERSION)
    assert installed() == {}


def test_two_installs_at_the_same_time_install_each_app_once(
    owner_pool: Engine, ledger: AppManifest, billing: AppManifest, query: Query
) -> None:
    """The migration lock makes the second install wait, then find nothing left to do."""
    start_together = threading.Barrier(2, timeout=10)
    counts: list[int] = []
    errors: list[BaseException] = []

    def run() -> None:
        """Start with the other thread, then install billing on a connection of its own."""
        try:
            with owner_pool.connect() as connection:
                start_together.wait()
                counts.append(
                    len(
                        install(
                            connection,
                            "billing",
                            catalog(ledger, billing),
                            ENGINE_VERSION,
                        )
                    )
                )
        except BaseException as error:
            errors.append(error)  # the main thread fails the test with it

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert errors == []
    assert sorted(counts) == [0, 3]
    rows = query(
        "SELECT module, count(*) AS n FROM core.applied_migration GROUP BY module"
    )
    assert {row.module: row.n for row in rows} == {"core": 1, "ledger": 1, "billing": 1}
