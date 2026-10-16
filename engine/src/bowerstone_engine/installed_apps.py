"""The record of installed apps and their permissions in the core schema (task 1.4).

core.installed_app holds each installed app: its version, the apps it needs,
and whether it is enabled. core.permission and core.role_permission hold the
permissions each app declares. Only the installer, as the owner role, writes
them. Every function here runs inside the caller's transaction.
"""

from dataclasses import dataclass

from sqlalchemy import Connection, text

from bowerstone_engine.apps import AppManifest
from bowerstone_engine.errors import InstallError


@dataclass(frozen=True)
class InstalledApp:
    """One row of core.installed_app.

    Attributes:
        key: The app's key.
        version: The version installed most recently.
        requires: The apps it needed at that version.
        is_enabled: False once the app is removed with its data kept.
    """

    key: str
    version: str
    requires: tuple[str, ...]
    is_enabled: bool


def read_installed(connection: Connection) -> dict[str, InstalledApp]:
    """Read every installed app, enabled or not.

    Args:
        connection: A connection as the owner role, inside a transaction.

    Returns:
        Each installed app, by key.

    Example:
        >>> read_installed(connection)["accounting"].is_enabled
        True
    """
    rows = connection.execute(
        text("SELECT key, version, requires, is_enabled FROM core.installed_app")
    ).all()
    return {
        row.key: InstalledApp(row.key, row.version, tuple(row.requires), row.is_enabled)
        for row in rows
    }


def record_installed(connection: Connection, manifest: AppManifest) -> None:
    """Record an app as installed and enabled, at its manifest's version.

    Args:
        connection: A connection as the owner role, inside a transaction.
        manifest: The app's manifest.

    Example:
        >>> record_installed(connection, invoicing_manifest)
    """
    connection.execute(
        text(
            "INSERT INTO core.installed_app (key, version, requires, is_enabled) "
            "VALUES (:key, :version, :requires, true) "
            "ON CONFLICT (key) DO UPDATE SET version = EXCLUDED.version, "
            "requires = EXCLUDED.requires, is_enabled = true"
        ),
        {
            "key": manifest.key,
            "version": manifest.version,
            "requires": list(manifest.requires),
        },
    )


def disable(connection: Connection, key: str) -> None:
    """Mark an app as removed, keeping its record, data, and permissions.

    Args:
        connection: A connection as the owner role, inside a transaction.
        key: The app's key.

    Example:
        >>> disable(connection, "invoicing")
    """
    connection.execute(
        text("UPDATE core.installed_app SET is_enabled = false WHERE key = :key"),
        {"key": key},
    )


def forget(connection: Connection, key: str) -> None:
    """Delete an app's record and its permissions, after its data is gone.

    Args:
        connection: A connection as the owner role, inside a transaction.
        key: The app's key.

    Example:
        >>> forget(connection, "invoicing")
    """
    connection.execute(
        text("DELETE FROM core.permission WHERE app_key = :key"), {"key": key}
    )
    connection.execute(
        text("DELETE FROM core.installed_app WHERE key = :key"), {"key": key}
    )


def _check_roles_exist(connection: Connection, manifest: AppManifest) -> None:
    """Refuse a manifest whose permissions name a role that does not exist.

    Args:
        connection: A connection as the owner role, inside a transaction.
        manifest: The app's manifest.

    Raises:
        InstallError: Naming the permission and the missing role.

    Example:
        >>> _check_roles_exist(connection, invoicing_manifest)
    """
    roles = set(connection.execute(text("SELECT key FROM core.role")).scalars())
    for permission in manifest.permissions:
        for role in permission.default_roles:
            if role not in roles:
                raise InstallError(
                    f"{manifest.key}: the permission {permission.key} names the role "
                    f"{role}, which does not exist"
                )


def sync_permissions(connection: Connection, manifest: AppManifest) -> None:
    """Make an app's permissions match its manifest.

    New permissions are added and given to their default roles. Descriptions
    are updated. Permissions the app no longer declares are removed, with their
    role assignments. Existing role assignments are never changed, so an
    owner's choices survive an upgrade.

    Args:
        connection: A connection as the owner role, inside a transaction.
        manifest: The app's manifest.

    Raises:
        InstallError: If a permission names a role that does not exist.

    Example:
        >>> sync_permissions(connection, invoicing_manifest)
    """
    _check_roles_exist(connection, manifest)
    declared = [permission.key for permission in manifest.permissions]
    connection.execute(
        text(
            "DELETE FROM core.permission WHERE app_key = :app AND NOT (key = ANY(:declared))"
        ),
        {"app": manifest.key, "declared": declared},
    )
    for permission in manifest.permissions:
        added = connection.execute(
            text(
                "INSERT INTO core.permission (key, app_key, description) "
                "VALUES (:key, :app, :description) "
                "ON CONFLICT (key) DO UPDATE SET description = EXCLUDED.description "
                "WHERE core.permission.app_key = EXCLUDED.app_key "
                "RETURNING (xmax = 0) AS added"
            ),
            {
                "key": permission.key,
                "app": manifest.key,
                "description": permission.description,
            },
        ).scalar_one()
        if added:
            for role in permission.default_roles:
                connection.execute(
                    text(
                        "INSERT INTO core.role_permission (role_key, permission_key) "
                        "VALUES (:role, :permission)"
                    ),
                    {"role": role, "permission": permission.key},
                )
