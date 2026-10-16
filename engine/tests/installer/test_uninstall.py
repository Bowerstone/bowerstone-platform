"""Tests for bowerstone_engine.installer.uninstall against PostgreSQL 18."""

import re
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, Row, text

from bowerstone_engine.apps import AppManifest
from bowerstone_engine.errors import InstallError
from bowerstone_engine.installer import install, uninstall

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


@pytest.fixture
def both(
    connection: Connection, ledger: AppManifest, billing: AppManifest
) -> dict[str, AppManifest]:
    """Ledger and billing, both installed."""
    apps = catalog(ledger, billing)
    install(connection, "billing", apps, ENGINE_VERSION)
    return apps


def test_an_app_another_needs_cannot_be_removed(
    connection: Connection, both: dict[str, AppManifest], installed: Installed
) -> None:
    """ledger cannot go while billing is enabled, and nothing changes."""
    with pytest.raises(
        InstallError, match="ledger cannot be removed while billing needs it"
    ):
        uninstall(connection, "ledger", both)
    assert installed() == {"ledger": ("0.1.0", True), "billing": ("0.1.0", True)}


def test_removing_and_restoring_an_app_keeps_its_data(
    connection: Connection,
    both: dict[str, AppManifest],
    owner_pool: Engine,
    installed: Installed,
    query: Query,
) -> None:
    """A removed app is disabled with its data kept, and installing it brings it all back."""
    with owner_pool.begin() as writer:
        writer.execute(text("INSERT INTO billing.item (name) VALUES ('Invoice 1')"))
    assert uninstall(connection, "billing", both) == []
    assert installed() == {"ledger": ("0.1.0", True), "billing": ("0.1.0", False)}
    assert [row.name for row in query("SELECT name FROM billing.item")] == ["Invoice 1"]
    assert len(query("SELECT 1 FROM core.permission WHERE app_key = 'billing'")) == 2

    assert install(connection, "billing", both, ENGINE_VERSION) == []
    assert installed() == {"ledger": ("0.1.0", True), "billing": ("0.1.0", True)}
    assert [row.name for row in query("SELECT name FROM billing.item")] == ["Invoice 1"]


def test_a_disabled_app_does_not_hold_its_dependency(
    connection: Connection, both: dict[str, AppManifest], installed: Installed
) -> None:
    """Once billing is removed, ledger may be removed too, keeping its data."""
    uninstall(connection, "billing", both)
    uninstall(connection, "ledger", both)
    assert installed() == {"ledger": ("0.1.0", False), "billing": ("0.1.0", False)}


def test_removing_an_app_twice_changes_nothing(
    connection: Connection, both: dict[str, AppManifest], installed: Installed
) -> None:
    """A second removal of an already removed app leaves it removed."""
    uninstall(connection, "billing", both)
    assert uninstall(connection, "billing", both) == []
    assert installed()["billing"] == ("0.1.0", False)


def test_deleting_an_apps_data_runs_its_down_migrations_and_forgets_it(
    connection: Connection,
    both: dict[str, AppManifest],
    schemas: Schemas,
    installed: Installed,
    query: Query,
) -> None:
    """billing's schema, record, migration records, and permissions all go."""
    undone = uninstall(connection, "billing", both, delete_data=True)
    assert [(item.module, item.migration.version) for item in undone] == [
        ("billing", 1)
    ]
    assert schemas() == {"core", "ledger"}
    assert installed() == {"ledger": ("0.1.0", True)}
    assert query("SELECT 1 FROM core.applied_migration WHERE module = 'billing'") == []
    assert query("SELECT 1 FROM core.permission WHERE app_key = 'billing'") == []
    assert (
        query(
            "SELECT 1 FROM core.role_permission WHERE permission_key LIKE 'billing.%'"
        )
        == []
    )


def test_data_that_may_not_be_deleted_is_kept(
    connection: Connection,
    both: dict[str, AppManifest],
    schemas: Schemas,
    installed: Installed,
) -> None:
    """ledger, like Accounting, refuses to have its data deleted, and stays installed."""
    uninstall(connection, "billing", both, delete_data=True)
    with pytest.raises(
        InstallError, match=re.escape("ledger's data may not be deleted")
    ):
        uninstall(connection, "ledger", both, delete_data=True)
    assert schemas() == {"core", "ledger"}
    assert installed() == {"ledger": ("0.1.0", True)}


def test_data_another_installed_app_needs_cannot_be_deleted(
    connection: Connection, app_factory: MakeApp, billing: AppManifest, schemas: Schemas
) -> None:
    """Even a disabled billing keeps data that relies on ledger, so ledger's data stays."""
    apps = catalog(app_factory("ledger", data_deletable=True), billing)
    install(connection, "billing", apps, ENGINE_VERSION)
    uninstall(connection, "billing", apps)
    with pytest.raises(
        InstallError, match="ledger data cannot be deleted while billing needs it"
    ):
        uninstall(connection, "ledger", apps, delete_data=True)
    assert schemas() == {"core", "ledger", "billing"}


def test_data_cannot_be_deleted_without_the_apps_package(
    connection: Connection, both: dict[str, AppManifest], ledger: AppManifest
) -> None:
    """The down migrations live in the package, so without it the data stays."""
    with pytest.raises(InstallError, match="without its package"):
        uninstall(connection, "billing", catalog(ledger), delete_data=True)


def test_an_app_that_is_not_installed_cannot_be_removed(
    connection: Connection, both: dict[str, AppManifest]
) -> None:
    """Removing an app that was never installed fails, naming it."""
    with pytest.raises(InstallError, match="payroll is not installed"):
        uninstall(connection, "payroll", both)
