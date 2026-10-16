"""Tests for bowerstone_engine.installer.upgrade_all against PostgreSQL 18."""

import re
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, Row, text

from bowerstone_engine.apps import AppManifest, Permission
from bowerstone_engine.errors import DatabaseCheckError, InstallError
from bowerstone_engine.installer import install, uninstall, upgrade_all

ENGINE_VERSION = "0.0.5"
MakeApp = Callable[..., AppManifest]
Query = Callable[..., Sequence[Row[Any]]]
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
def with_second_migration(
    migrations_for: Callable[[str], dict[str, str]],
) -> Callable[[str], dict[str, str]]:
    """Give an app its first migration unchanged, plus a second one."""
    return lambda key: {
        **migrations_for(key),
        "0002_extra.sql": f"CREATE TABLE {key}.extra (id bigint PRIMARY KEY);\n",
        "0002_extra.down.sql": f"DROP TABLE {key}.extra;\n",
    }


SecondMigration = Callable[[str], dict[str, str]]


def test_a_new_database_gets_the_core_migrations(connection: Connection) -> None:
    """With no apps installed, upgrade_all checks the database and applies core."""
    applied = upgrade_all(connection, {}, ENGINE_VERSION)
    assert [(item.module, item.migration.version) for item in applied] == [("core", 1)]


def test_the_database_check_runs_first(
    database_factory: Callable[[str], str],
    app_url_for: Callable[[str], Any],
    pool_factory: Callable[..., Engine],
) -> None:
    """Connected as the app role, upgrade_all is refused before anything changes."""
    pool = pool_factory(app_url_for(database_factory("builtin")))
    with pool.connect() as connection, pytest.raises(DatabaseCheckError):
        upgrade_all(connection, {}, ENGINE_VERSION)


def test_only_new_migrations_run_and_the_version_is_recorded(
    with_second_migration: SecondMigration,
    connection: Connection,
    ledger: AppManifest,
    app_factory: MakeApp,
    installed: Installed,
) -> None:
    """A new ledger version with one more migration applies only that one."""
    install(connection, "ledger", catalog(ledger), ENGINE_VERSION)
    newer = app_factory(
        "ledger",
        version="0.2.0",
        migrations=with_second_migration("ledger"),
        data_deletable=False,
    )
    applied = upgrade_all(connection, catalog(newer), ENGINE_VERSION)
    assert [(item.module, item.migration.version) for item in applied] == [
        ("ledger", 2)
    ]
    assert installed() == {"ledger": ("0.2.0", True)}


def test_a_disabled_app_is_not_upgraded(
    with_second_migration: SecondMigration,
    connection: Connection,
    ledger: AppManifest,
    billing: AppManifest,
    app_factory: MakeApp,
    query: Query,
) -> None:
    """A removed app's new migrations wait until it is installed again."""
    install(connection, "billing", catalog(ledger, billing), ENGINE_VERSION)
    uninstall(connection, "billing", catalog(ledger, billing))
    newer = app_factory(
        "billing",
        version="0.2.0",
        requires=("ledger",),
        migrations=with_second_migration("billing"),
    )
    assert upgrade_all(connection, catalog(ledger, newer), ENGINE_VERSION) == []
    assert [
        row.version
        for row in query(
            "SELECT version FROM core.applied_migration WHERE module = 'billing'"
        )
    ] == [1]


def test_an_enabled_app_without_its_package_stops_the_upgrade(
    connection: Connection, ledger: AppManifest, billing: AppManifest
) -> None:
    """An enabled app whose package is gone cannot be checked, so the upgrade stops."""
    install(connection, "billing", catalog(ledger, billing), ENGINE_VERSION)
    with pytest.raises(
        InstallError,
        match="billing is installed and enabled, but its package is missing",
    ):
        upgrade_all(connection, catalog(ledger), ENGINE_VERSION)


def test_an_engine_version_outside_an_enabled_apps_range_stops_the_upgrade(
    with_second_migration: SecondMigration,
    connection: Connection,
    ledger: AppManifest,
    app_factory: MakeApp,
) -> None:
    """A new ledger package that needs a newer engine is refused, and nothing is applied."""
    install(connection, "ledger", catalog(ledger), ENGINE_VERSION)
    newer = app_factory(
        "ledger",
        version="0.2.0",
        migrations=with_second_migration("ledger"),
        engine_versions=">=0.1,<0.2",
    )
    with pytest.raises(InstallError, match=re.escape("not this engine's 0.0.5")):
        upgrade_all(connection, catalog(newer), ENGINE_VERSION)


def test_a_new_dependency_must_be_installed_first(
    connection: Connection,
    ledger: AppManifest,
    billing: AppManifest,
    app_factory: MakeApp,
) -> None:
    """upgrade_all never installs an app on its own; it says which one to install."""
    install(connection, "billing", catalog(ledger, billing), ENGINE_VERSION)
    extra = app_factory("extra")
    newer = app_factory("billing", version="0.2.0", requires=("ledger", "extra"))
    with pytest.raises(InstallError, match="now needs extra, which is not installed"):
        upgrade_all(connection, catalog(ledger, newer, extra), ENGINE_VERSION)


def test_permissions_follow_the_manifest_but_keep_the_owners_choices(
    connection: Connection,
    ledger: AppManifest,
    app_factory: MakeApp,
    owner_pool: Engine,
    query: Query,
) -> None:
    """New permissions are added, changed ones updated, dropped ones removed, and role choices kept."""
    install(connection, "ledger", catalog(ledger), ENGINE_VERSION)
    with owner_pool.begin() as owner:
        owner.execute(
            text(
                "DELETE FROM core.role_permission WHERE role_key = 'staff' AND permission_key = 'ledger.view'"
            )
        )
    newer = app_factory(
        "ledger",
        version="0.2.0",
        data_deletable=False,
        permissions=(
            Permission(
                "ledger.view", "See the ledger", ("owner", "staff", "read_only")
            ),
            Permission("ledger.export", "Export the ledger", ("owner", "staff")),
        ),
    )
    upgrade_all(connection, catalog(newer), ENGINE_VERSION)
    permissions = query(
        "SELECT key, description FROM core.permission WHERE app_key = 'ledger'"
    )
    assert {tuple(row) for row in permissions} == {
        ("ledger.view", "See the ledger"),
        ("ledger.export", "Export the ledger"),
    }
    held = query(
        "SELECT role_key, permission_key FROM core.role_permission WHERE permission_key LIKE 'ledger.%'"
    )
    assert {tuple(row) for row in held} == {
        ("owner", "ledger.view"),
        ("read_only", "ledger.view"),
        ("owner", "ledger.export"),
        ("staff", "ledger.export"),
    }
