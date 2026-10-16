"""App manifests: what each Bowerstone app declares about itself (task 1.4).

Every app package declares one AppManifest under the Python entry point group
bowerstone.apps, named after the app's key:

    [project.entry-points."bowerstone.apps"]
    invoicing = "bowerstone_invoicing.manifest:APP"

The engine finds them with discover_apps, and the installer uses them to
install, upgrade, and remove apps. Apps import this module.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import entry_points

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from bowerstone_engine.errors import AppManifestError

ENTRY_POINT_GROUP = "bowerstone.apps"
APP_KEY = re.compile(r"[a-z][a-z0-9_]{1,30}")
ROLE_KEY = re.compile(r"[a-z][a-z0-9_]{1,30}")
PERMISSION_NAME = re.compile(r"[a-z][a-z0-9_]{0,60}")
PACKAGE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")
# Names the engine already uses for its own schema, URL paths, and modules.
RESERVED_KEYS = frozenset(
    {"core", "public", "api", "apps", "admin", "auth", "static", "engine"}
)


def check_app_key(key: object) -> str:
    """Refuse an app key that is not usable as a schema name and URL prefix.

    Args:
        key: The proposed key.

    Returns:
        The key, once it has passed.

    Raises:
        AppManifestError: If the key is not 2 to 31 lower case letters, digits,
            and underscores starting with a letter, is reserved, or starts with
            pg_, which PostgreSQL keeps for its own schemas.

    Example:
        >>> check_app_key("invoicing")
        'invoicing'
    """
    if not isinstance(key, str) or not APP_KEY.fullmatch(key):
        raise AppManifestError(f"{key!r} is not a valid app key")
    if key in RESERVED_KEYS or key.startswith("pg_"):
        raise AppManifestError(f"{key!r} is reserved and cannot be an app key")
    return key


def check_version_range(app: str, label: str, value: object) -> SpecifierSet:
    """Read a version range such as >=0.1,<0.2.

    Args:
        app: The app's key, for messages.
        label: Which range it is, for messages.
        value: The range as written in the manifest.

    Returns:
        The parsed range.

    Raises:
        AppManifestError: If the value is not a non-empty, valid range.

    Example:
        >>> "0.1.4" in check_version_range("invoicing", "engine_versions", ">=0.1,<0.2")
        True
    """
    if not isinstance(value, str) or not value.strip():
        raise AppManifestError(
            f"{app}: {label} must be a version range, such as >=0.1,<0.2"
        )
    try:
        return SpecifierSet(value)
    except InvalidSpecifier:
        raise AppManifestError(
            f"{app}: {label} {value!r} is not a valid version range"
        ) from None


@dataclass(frozen=True)
class Permission:
    """A permission an app adds to the core permission tables.

    Attributes:
        key: The app's key, a dot, and a name, such as invoicing.issue.
        description: What the permission allows, for the roles screen.
        default_roles: The roles that hold it when it is first added.
    """

    key: str
    description: str
    default_roles: tuple[str, ...]

    def __post_init__(self) -> None:
        """Refuse a malformed key, description, or role list.

        Raises:
            AppManifestError: Naming the permission and the problem.

        Example:
            >>> Permission("invoicing.issue", "Issue invoices", ("owner", "staff")).key
            'invoicing.issue'
        """
        app, _, name = (
            self.key.partition(".") if isinstance(self.key, str) else ("", "", "")
        )
        if not APP_KEY.fullmatch(app) or not PERMISSION_NAME.fullmatch(name):
            raise AppManifestError(
                f"{self.key!r} is not a valid permission key, such as invoicing.issue"
            )
        if (
            not isinstance(self.description, str)
            or not 1 <= len(self.description.strip()) <= 200
        ):
            raise AppManifestError(
                f"{self.key}: the description must be 1 to 200 characters"
            )
        if not isinstance(self.default_roles, tuple):
            raise AppManifestError(
                f"{self.key}: default_roles must be a tuple of role keys"
            )
        for role in self.default_roles:
            if not isinstance(role, str) or not ROLE_KEY.fullmatch(role):
                raise AppManifestError(f"{self.key}: {role!r} is not a valid role key")
        if len(set(self.default_roles)) != len(self.default_roles):
            raise AppManifestError(f"{self.key}: a role is listed twice")


@dataclass(frozen=True)
class AppManifest:
    """What an app declares about itself.

    Attributes:
        key: The app's key, also its schema name and URL prefix, such as invoicing.
        name: The app's name for people, such as Invoicing.
        version: The app's version, such as 0.1.0.
        migrations_package: The package holding its migration files.
        requires: The keys of the apps it needs.
        engine_versions: The engine versions it works with, such as >=0.1,<0.2.
        ui_versions: The design system versions its screens work with.
        permissions: The permissions it adds.
        data_deletable: Whether its data may be deleted when it is removed.
            False for Accounting, whose records must be kept.
    """

    key: str
    name: str
    version: str
    migrations_package: str
    requires: tuple[str, ...]
    engine_versions: str
    ui_versions: str
    permissions: tuple[Permission, ...]
    data_deletable: bool

    def __post_init__(self) -> None:
        """Refuse a manifest that breaks the rules every manifest must meet.

        Raises:
            AppManifestError: Naming the app and the first problem found.

        Example:
            >>> AppManifest(key="core", ...)
            Traceback (most recent call last):
            AppManifestError: 'core' is reserved and cannot be an app key
        """
        key = check_app_key(self.key)
        if not isinstance(self.name, str) or not 1 <= len(self.name.strip()) <= 100:
            raise AppManifestError(f"{key}: the name must be 1 to 100 characters")
        try:
            Version(self.version)
        except (InvalidVersion, TypeError):
            raise AppManifestError(
                f"{key}: {self.version!r} is not a valid version"
            ) from None
        if not isinstance(self.migrations_package, str) or not PACKAGE_NAME.fullmatch(
            self.migrations_package
        ):
            raise AppManifestError(
                f"{key}: {self.migrations_package!r} is not a package name"
            )
        check_version_range(key, "engine_versions", self.engine_versions)
        check_version_range(key, "ui_versions", self.ui_versions)
        if not isinstance(self.data_deletable, bool):
            raise AppManifestError(f"{key}: data_deletable must be True or False")
        self._check_requires(key)
        self._check_permissions(key)

    def _check_requires(self, key: str) -> None:
        """Refuse a requires list that is not a tuple of other, distinct app keys.

        Args:
            key: The app's key.

        Raises:
            AppManifestError: Naming the problem.

        Example:
            >>> manifest._check_requires("invoicing")
        """
        if not isinstance(self.requires, tuple):
            raise AppManifestError(f"{key}: requires must be a tuple of app keys")
        for needed in self.requires:
            check_app_key(needed)
            if needed == key:
                raise AppManifestError(f"{key}: an app cannot require itself")
        if len(set(self.requires)) != len(self.requires):
            raise AppManifestError(f"{key}: an app is listed twice in requires")

    def _check_permissions(self, key: str) -> None:
        """Refuse permissions that are not this app's, or that repeat.

        Args:
            key: The app's key.

        Raises:
            AppManifestError: Naming the permission.

        Example:
            >>> manifest._check_permissions("invoicing")
        """
        if not isinstance(self.permissions, tuple):
            raise AppManifestError(f"{key}: permissions must be a tuple of Permission")
        seen: set[str] = set()
        for permission in self.permissions:
            if not isinstance(permission, Permission):
                raise AppManifestError(f"{key}: {permission!r} is not a Permission")
            if not permission.key.startswith(f"{key}."):
                raise AppManifestError(
                    f"{key}: the permission {permission.key} must start with {key}."
                )
            if permission.key in seen:
                raise AppManifestError(
                    f"{key}: the permission {permission.key} is listed twice"
                )
            seen.add(permission.key)

    def supports_engine(self, engine_version: str) -> bool:
        """Say whether this app works with an engine version.

        Args:
            engine_version: The engine's version, such as 0.1.3.

        Returns:
            True when the version is inside engine_versions.

        Example:
            >>> manifest.supports_engine("0.1.3")
            True
        """
        return Version(engine_version) in SpecifierSet(self.engine_versions)


def discover_apps() -> Mapping[str, AppManifest]:
    """Find the manifest of every app package installed with the engine.

    Returns:
        Each app's manifest, by key.

    Raises:
        AppManifestError: If an entry point cannot be loaded, is not an
            AppManifest, is not named after its app's key, or two packages
            declare the same app.

    Example:
        >>> sorted(discover_apps())
        ['accounting', 'invoicing']
    """
    found: dict[str, AppManifest] = {}
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        try:
            manifest = entry.load()
        except Exception as error:
            raise AppManifestError(
                f"the app entry point {entry.name} = {entry.value} could not be loaded: {error}"
            ) from error
        if not isinstance(manifest, AppManifest):
            raise AppManifestError(
                f"the app entry point {entry.name} = {entry.value} is not an AppManifest"
            )
        if entry.name != manifest.key:
            raise AppManifestError(
                f"the app entry point {entry.name} must be named after its key, {manifest.key}"
            )
        if manifest.key in found:
            raise AppManifestError(
                f"two installed packages declare the app {manifest.key}"
            )
        found[manifest.key] = manifest
    return found
