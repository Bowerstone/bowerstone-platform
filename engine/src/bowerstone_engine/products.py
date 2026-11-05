"""Product manifests, and finding the products present on this computer.

Each product's package declares one ProductManifest under the entry point
group `bowerstone.products`, named after the product's key:

    [project.entry-points."bowerstone.products"]
    accounting = "bowerstone_accounting.manifest:PRODUCT"

Products may import this module. It is part of the engine's SDK.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

ENTRY_POINT_GROUP = "bowerstone.products"
ENGINE_DISTRIBUTION = "bowerstone-engine"

# A key is also the product's schema name and its URL prefix.
KEY_PATTERN = re.compile(r"[a-z][a-z0-9_]{1,30}")
RESERVED_KEYS = frozenset(
    {
        "api",
        "bowerstone",
        "core",
        "health",
        "information_schema",
        "products",
        "public",
        "static",
    }
)

_ROLE_PATTERN = re.compile(r"[a-z][a-z0-9_]*")
_PERMISSION_PATTERN = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+")


class ProductError(Exception):
    """A product cannot be loaded, installed, or removed. The message says why."""


@dataclass(frozen=True)
class Permission:
    """A permission a product adds, with the roles that get it on install."""

    key: str
    description: str
    roles: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ProductManifest:
    """What a product tells the engine about itself."""

    # Lower case, 2 to 31 characters. Also the product's schema and URL prefix.
    key: str
    name: str
    version: str
    # The package holding the product's numbered migration files.
    migrations_package: str
    # The engine versions it works with, such as ">=0.1,<0.2".
    engine_versions: str
    # The screen kit versions its screens work with.
    ui_versions: str
    # Builds the product's API routes.
    router: Callable[[], object] | None = None
    # The package holding the product's built screens.
    screens_package: str | None = None
    # The keys of the other products it needs.
    requires: tuple[str, ...] = ()
    permissions: tuple[Permission, ...] = ()
    # Whether removing the product may delete its data. Accounting's never may.
    data_deletable: bool = False

    def problems(self, engine_version: str) -> list[str]:
        """Every rule this manifest breaks, as plain sentences."""
        return [
            *self._key_problems(),
            *self._detail_problems(),
            *self._version_problems(engine_version),
            *self._permission_problems(),
        ]

    def _key_problems(self) -> list[str]:
        if not KEY_PATTERN.fullmatch(self.key):
            return [
                f"The product key {self.key!r} must be 2 to 31 characters: a "
                "lower-case letter, then lower-case letters, digits, or underscores."
            ]
        if self.key in RESERVED_KEYS or self.key.startswith("pg_"):
            return [f"The product key {self.key!r} is reserved."]
        return []

    def _detail_problems(self) -> list[str]:
        problems = []
        if not self.name.strip():
            problems.append(f"The product {self.key} has no name.")
        if not self.migrations_package.strip():
            problems.append(f"The product {self.key} names no migrations package.")
        if self.key in self.requires:
            problems.append(f"The product {self.key} needs itself.")
        if len(set(self.requires)) != len(self.requires):
            problems.append(f"The product {self.key} lists a product it needs twice.")
        return problems

    def _version_problems(self, engine_version: str) -> list[str]:
        problems = []
        try:
            Version(self.version)
        except InvalidVersion:
            problems.append(
                f"The product {self.key}'s version {self.version!r} "
                "is not a version number."
            )
        engine_range = _version_range(self.engine_versions)
        if engine_range is None:
            problems.append(
                f"The product {self.key}'s engine versions {self.engine_versions!r} "
                "are not a version range, such as '>=0.1,<0.2'."
            )
        elif not engine_range.contains(engine_version, prereleases=True):
            problems.append(
                f"The product {self.key} works with engine versions "
                f"{self.engine_versions}, and this engine is {engine_version}."
            )
        if _version_range(self.ui_versions) is None:
            problems.append(
                f"The product {self.key}'s screen kit versions {self.ui_versions!r} "
                "are not a version range, such as '>=0.1,<0.2'."
            )
        return problems

    def _permission_problems(self) -> list[str]:
        keys = [permission.key for permission in self.permissions]
        problems = [
            f"The product {self.key} lists the permission {key} more than once."
            for key in sorted({key for key in keys if keys.count(key) > 1})
        ]
        for permission in self.permissions:
            if not (
                _PERMISSION_PATTERN.fullmatch(permission.key)
                and permission.key.startswith(f"{self.key}.")
            ):
                problems.append(
                    f"The product {self.key}'s permission {permission.key!r} must "
                    f"start with {self.key}., as in {self.key}.view."
                )
            if not permission.description.strip():
                problems.append(f"The permission {permission.key} has no description.")
            problems.extend(
                f"The permission {permission.key} names the role {role!r}, "
                "which is not a role name."
                for role in permission.roles
                if not _ROLE_PATTERN.fullmatch(role)
            )
        return problems


def engine_version() -> str:
    """This engine's version, from its installed package."""
    return metadata.version(ENGINE_DISTRIBUTION)


def load_products() -> dict[str, ProductManifest]:
    """Every product whose package is present, by key, from the entry points.

    Raises ProductError if a product's package cannot be loaded, or its entry
    point does not hold a ProductManifest named after it.
    """
    products: dict[str, ProductManifest] = {}
    problems: list[str] = []
    entry_points = metadata.entry_points(group=ENTRY_POINT_GROUP)
    for entry_point in sorted(entry_points, key=lambda entry_point: entry_point.name):
        try:
            manifest = entry_point.load()
        except Exception as error:  # A broken package may raise anything.
            problems.append(
                f"The product {entry_point.name} could not be loaded: {error}"
            )
            continue
        if not isinstance(manifest, ProductManifest):
            problems.append(
                f"The product entry point {entry_point.name} ({entry_point.value}) "
                "does not hold a ProductManifest."
            )
        elif manifest.key != entry_point.name:
            problems.append(
                f"The product entry point {entry_point.name} holds the product "
                f"{manifest.key}. Their names must match."
            )
        elif manifest.key in products:
            problems.append(f"Two packages declare the product {manifest.key}.")
        else:
            products[manifest.key] = manifest
    if problems:
        raise ProductError(" ".join(problems))
    return products


def _version_range(text: str) -> SpecifierSet | None:
    if not text.strip():
        return None
    try:
        return SpecifierSet(text)
    except InvalidSpecifier:
        return None
