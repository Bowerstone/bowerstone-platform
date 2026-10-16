"""Installing, upgrading, and removing apps (Platform Build Plan, task 1.4).

The three functions here are what `bowerstone migrate` and `bowerstone apps`
run (task 1.6), as the owner role. Each holds the migration lock from start to
end, so two of them never run at once. Each takes the manifests of the app
packages that are installed, and the engine's own version.
"""

from collections.abc import Mapping, Sequence
from importlib.resources import files
from typing import NamedTuple

from packaging.version import InvalidVersion, Version
from sqlalchemy import Connection, text

from bowerstone_engine.app_dependencies import install_order
from bowerstone_engine.apps import AppManifest
from bowerstone_engine.database_check import check_database
from bowerstone_engine.errors import InstallError
from bowerstone_engine.installed_apps import (
    InstalledApp,
    disable,
    forget,
    read_installed,
    record_installed,
    sync_permissions,
)
from bowerstone_engine.migration_runner.migrate import downgrade, upgrade
from bowerstone_engine.migration_runner.migration_execution import migration_lock
from bowerstone_engine.migration_runner.migration_files import (
    Migration,
    read_migrations,
)


class ModuleMigration(NamedTuple):
    """A migration and the module, core or an app, it belongs to."""

    module: str
    migration: Migration


def _app_migrations(manifest: AppManifest) -> list[Migration]:
    """Read an app's migrations from its package.

    Args:
        manifest: The app's manifest.

    Returns:
        The app's migrations, from 0001.

    Raises:
        InstallError: If the migrations package cannot be found.
        MigrationError: If its files break the migration rules.

    Example:
        >>> [m.name for m in _app_migrations(invoicing_manifest)]
        ['invoicing']
    """
    try:
        folder = files(manifest.migrations_package)
    except ModuleNotFoundError:
        raise InstallError(
            f"{manifest.key}: its migrations package {manifest.migrations_package} is missing"
        ) from None
    return read_migrations(manifest.key, folder)


def _check_engine_version(
    manifests: Sequence[AppManifest], engine_version: str
) -> None:
    """Refuse when the engine's version is outside any of these apps' ranges.

    Args:
        manifests: The apps about to be installed or upgraded.
        engine_version: The engine's version, such as 0.1.3.

    Raises:
        InstallError: Naming the app and its range, or if the version is invalid.

    Example:
        >>> _check_engine_version([invoicing_manifest], "0.1.3")
    """
    try:
        Version(engine_version)
    except InvalidVersion:
        raise InstallError(
            f"{engine_version!r} is not a valid engine version"
        ) from None
    for manifest in manifests:
        if not manifest.supports_engine(engine_version):
            raise InstallError(
                f"{manifest.key} {manifest.version} works with engine versions "
                f"{manifest.engine_versions}, not this engine's {engine_version}"
            )


def _upgrade_core(connection: Connection) -> list[ModuleMigration]:
    """Run the database check, then the core migrations the database lacks.

    Args:
        connection: A connection as the owner role, with no open transaction.

    Returns:
        The core migrations applied.

    Raises:
        DatabaseCheckError: If the database is not one Bowerstone may run against.

    Example:
        >>> _upgrade_core(connection)
        []
    """
    with connection.begin():
        check_database(connection)
    core = read_migrations("core", files("bowerstone_engine.core.migrations"))
    return [ModuleMigration("core", m) for m in upgrade(connection, "core", core)]


def _upgrade_app(
    connection: Connection, manifest: AppManifest
) -> list[ModuleMigration]:
    """Apply an app's new migrations, then record it and its permissions.

    Args:
        connection: A connection as the owner role, with no open transaction.
        manifest: The app's manifest.

    Returns:
        The app's migrations applied.

    Raises:
        InstallError: If the app has no schema named after its key afterwards,
            or a permission names a role that does not exist.

    Example:
        >>> _upgrade_app(connection, invoicing_manifest)
    """
    applied = upgrade(connection, manifest.key, _app_migrations(manifest))
    with connection.begin():
        has_schema = connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :key)"),
            {"key": manifest.key},
        ).scalar_one()
        if not has_schema:
            raise InstallError(
                f"{manifest.key}: its first migration must create the schema {manifest.key}"
            )
        record_installed(connection, manifest)
        sync_permissions(connection, manifest)
    return [ModuleMigration(manifest.key, m) for m in applied]


def upgrade_all(
    connection: Connection, available: Mapping[str, AppManifest], engine_version: str
) -> list[ModuleMigration]:
    """Bring the database up to date: core first, then every enabled app.

    Args:
        connection: A connection as bowerstone_owner, with no open transaction.
        available: The manifests of the installed app packages, by key.
        engine_version: The engine's version.

    Returns:
        Every migration applied, in the order applied.

    Raises:
        DatabaseCheckError: If the database is not one Bowerstone may run against.
        InstallError: If an enabled app's package is missing, or the engine's
            version is outside an enabled app's range.

    Example:
        >>> upgrade_all(connection, discover_apps(), "0.1.0")
    """
    with migration_lock(connection):
        applied = _upgrade_core(connection)
        with connection.begin():
            installed = read_installed(connection)
        enabled = sorted(key for key, app in installed.items() if app.is_enabled)
        for key in enabled:
            if key not in available:
                raise InstallError(
                    f"{key} is installed and enabled, but its package is missing; "
                    "install the package again, or uninstall the app"
                )
        order = install_order(enabled, available)
        newly_needed = [key for key in order if key not in enabled]
        if newly_needed:
            raise InstallError(
                f"an enabled app now needs {', '.join(newly_needed)}, which is not "
                "installed; install it with bowerstone apps install first"
            )
        _check_engine_version([available[key] for key in order], engine_version)
        for key in order:
            applied += _upgrade_app(connection, available[key])
    return applied


def install(
    connection: Connection,
    key: str,
    available: Mapping[str, AppManifest],
    engine_version: str,
) -> list[ModuleMigration]:
    """Install an app and every app it needs, or restore a removed one.

    Args:
        connection: A connection as bowerstone_owner, with no open transaction.
        key: The app to install.
        available: The manifests of the installed app packages, by key.
        engine_version: The engine's version.

    Returns:
        Every migration applied, core and apps, in the order applied.

    Raises:
        DatabaseCheckError: If the database is not one Bowerstone may run against.
        InstallError: If the app or one it needs has no package, apps need each
            other in a loop, or the engine's version is outside a range. All of
            this is checked before any migration runs.

    Example:
        >>> install(connection, "invoicing", discover_apps(), "0.1.0")
    """
    order = install_order([key], available)
    _check_engine_version([available[name] for name in order], engine_version)
    with migration_lock(connection):
        applied = _upgrade_core(connection)
        for name in order:
            applied += _upgrade_app(connection, available[name])
    return applied


def _refuse_while_needed(
    installed: Mapping[str, InstalledApp], key: str, by_enabled_only: bool
) -> None:
    """Refuse to remove an app that other installed apps need.

    Args:
        installed: The installed apps, by key, as from read_installed.
        key: The app to remove.
        by_enabled_only: True to count only enabled apps as needing it.

    Raises:
        InstallError: Naming the apps that need it.

    Example:
        >>> _refuse_while_needed(installed, "accounting", by_enabled_only=True)
    """
    needed_by = sorted(
        name
        for name, app in installed.items()
        if key in app.requires and (app.is_enabled or not by_enabled_only)
    )
    if needed_by:
        what = "data cannot be deleted" if not by_enabled_only else "cannot be removed"
        raise InstallError(
            f"{key} {what} while {', '.join(needed_by)} needs it; remove that first"
        )


def uninstall(
    connection: Connection,
    key: str,
    available: Mapping[str, AppManifest],
    delete_data: bool = False,
) -> list[ModuleMigration]:
    """Remove an app, keeping its data unless told to delete it.

    Args:
        connection: A connection as bowerstone_owner, with no open transaction.
        key: The app to remove.
        available: The manifests of the installed app packages, by key.
        delete_data: True to run the app's down migrations and forget it.

    Returns:
        The down migrations run, newest first. Empty when the data is kept.

    Raises:
        InstallError: If the app is not installed, an enabled app needs it, or
            its data is to be deleted while any installed app needs it, while
            its package is missing, or when its manifest forbids it.

    Example:
        >>> uninstall(connection, "invoicing", discover_apps())
        []
    """
    with migration_lock(connection):
        with connection.begin():
            installed = read_installed(connection)
            if key not in installed:
                raise InstallError(f"{key} is not installed")
            _refuse_while_needed(installed, key, by_enabled_only=True)
            if not delete_data:
                disable(connection, key)
                return []
            _refuse_while_needed(installed, key, by_enabled_only=False)
        manifest = available.get(key)
        if manifest is None:
            raise InstallError(
                f"{key}'s data cannot be deleted without its package, which holds its down migrations"
            )
        if not manifest.data_deletable:
            raise InstallError(
                f"{key}'s data may not be deleted; its records must be kept"
            )
        undone = downgrade(connection, key, _app_migrations(manifest), 0)
        with connection.begin():
            forget(connection, key)
    return [ModuleMigration(key, m) for m in undone]
