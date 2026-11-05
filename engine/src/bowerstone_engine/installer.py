"""Installing, updating, and removing products.

Each function takes an engine that connects as the owner role, and works in
one migration session: the owner form of the database check runs first, and
the migration lock is held for the whole operation, so its own changes cannot
interleave with another run's. `products` is the catalog of
products present, by key. It defaults to the products found through their
entry points.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import Connection, Engine, text

from bowerstone_engine.migrate import (
    Migration,
    MigrationSession,
    MigrationSet,
    core_migrations,
    migration_session,
    migrations_from_package,
)
from bowerstone_engine.products import (
    ProductError,
    ProductManifest,
    engine_version,
    load_products,
)


@dataclass(frozen=True)
class InstalledProduct:
    """A row of core.installed_product."""

    key: str
    version: str
    requires: tuple[str, ...]
    is_enabled: bool


def upgrade_all(
    engine: Engine, products: Mapping[str, ProductManifest] | None = None
) -> list[Migration]:
    """Brings the database up to date.

    Runs the database check, then the core's migrations, then each enabled
    product's new migrations, each product after the products it needs. Stops
    if an enabled product's package is missing. Records each enabled product's
    current version and permissions. Returns the migrations applied.
    """
    catalog = _catalog(products)
    with migration_session(engine) as session:
        applied = session.upgrade([core_migrations()])
        installed = installed_products(session.connection)
        enabled = sorted(
            key for key, product in installed.items() if product.is_enabled
        )
        missing = [key for key in enabled if key not in catalog]
        if missing:
            raise ProductError(
                " ".join(
                    f"The product {key} is installed, but its package is missing."
                    for key in missing
                )
            )
        for key in enabled:
            for needed in catalog[key].requires:
                if needed not in enabled:
                    raise ProductError(
                        f"The product {key} needs {needed}, which is not installed. "
                        f"Install it with: bowerstone products install {needed}"
                    )
        order = dependency_order(enabled, catalog)
        return applied + _bring_up(session, [catalog[key] for key in order])


def install(
    engine: Engine, key: str, products: Mapping[str, ProductManifest] | None = None
) -> list[str]:
    """Installs the product `key` and every product it needs, each after its needs.

    Applies their migrations and registers their permissions. A removed
    product comes back with its data. Returns the keys of the products that
    were not installed and enabled before, in the order they were installed.
    """
    catalog = _catalog(products)
    order = dependency_order([key], catalog)
    with migration_session(engine) as session:
        session.upgrade([core_migrations()])
        before = installed_products(session.connection)
        _bring_up(session, [catalog[each] for each in order])
    return [each for each in order if not (each in before and before[each].is_enabled)]


def uninstall(
    engine: Engine,
    key: str,
    *,
    delete_data: bool = False,
    products: Mapping[str, ProductManifest] | None = None,
) -> None:
    """Removes the product `key`.

    Refused while an enabled product needs it. Without `delete_data`, the
    product is disabled and its data kept, so installing it again restores it.
    With `delete_data`, its down migrations run and its permissions go, which
    is refused for a product whose data may never be deleted.
    """
    with migration_session(engine) as session:
        installed = installed_products(session.connection)
        if key not in installed:
            raise ProductError(f"The product {key} is not installed.")
        needed_by = sorted(
            other
            for other, product in installed.items()
            if product.is_enabled and key in product.requires
        )
        if needed_by:
            raise ProductError(
                f"The product {key} cannot be removed while {_listed(needed_by)} "
                f"{'needs' if len(needed_by) == 1 else 'need'} it. Remove "
                f"{'it' if len(needed_by) == 1 else 'them'} first."
            )
        if not delete_data:
            with session.transaction() as conn:
                conn.execute(
                    text(
                        "UPDATE core.installed_product SET is_enabled = false "
                        "WHERE key = :key AND is_enabled"
                    ),
                    {"key": key},
                )
            return

        manifest = _catalog(products).get(key)
        if manifest is None:
            raise ProductError(
                f"The data of the product {key} cannot be deleted, "
                "because its package is missing."
            )
        if not manifest.data_deletable:
            raise ProductError(f"The data of the product {key} may never be deleted.")
        session.downgrade(_migrations(manifest), to_version=0)
        with session.transaction() as conn:
            conn.execute(
                text("DELETE FROM core.permission WHERE module = :key"), {"key": key}
            )
            conn.execute(
                text("DELETE FROM core.installed_product WHERE key = :key"),
                {"key": key},
            )


def dependency_order(
    keys: Iterable[str], products: Mapping[str, ProductManifest]
) -> list[str]:
    """`keys` and every product they need, each after the products it needs.

    Raises ProductError for a product whose package is not installed, or for
    products that need each other in a loop.
    """
    order: list[str] = []
    done: set[str] = set()

    def visit(key: str, path: list[str]) -> None:
        if key in done:
            return
        if key in path:
            loop = [*path[path.index(key) :], key]
            raise ProductError(
                f"These products need each other in a loop: {', '.join(loop)}."
            )
        manifest = products.get(key)
        if manifest is None:
            if path:
                raise ProductError(
                    f"The product {path[-1]} needs {key}, "
                    "whose package is not installed."
                )
            raise ProductError(f"The product {key}'s package is not installed.")
        for needed in manifest.requires:
            visit(needed, [*path, key])
        done.add(key)
        order.append(key)

    for key in keys:
        visit(key, [])
    return order


def installed_products(conn: Connection) -> dict[str, InstalledProduct]:
    """Every product in core.installed_product, enabled or not, by key.

    Before the core is migrated, the table does not exist, so nothing is installed.
    """
    if (
        conn.execute(text("SELECT to_regclass('core.installed_product')")).scalar()
        is None
    ):
        return {}
    rows = conn.execute(
        text("SELECT key, version, requires, is_enabled FROM core.installed_product")
    )
    return {
        row.key: InstalledProduct(
            row.key, row.version, tuple(row.requires), row.is_enabled
        )
        for row in rows
    }


def _catalog(
    products: Mapping[str, ProductManifest] | None,
) -> Mapping[str, ProductManifest]:
    return load_products() if products is None else products


def _bring_up(
    session: MigrationSession, manifests: Sequence[ProductManifest]
) -> list[Migration]:
    """Checks the manifests, applies their migrations, and records them as installed."""
    version = engine_version()
    problems = [
        problem for manifest in manifests for problem in manifest.problems(version)
    ]
    if problems:
        raise ProductError(" ".join(problems))
    applied = session.upgrade([_migrations(manifest) for manifest in manifests])
    for manifest in manifests:
        if not session.connection.execute(
            text("SELECT to_regnamespace(:key) IS NOT NULL"), {"key": manifest.key}
        ).scalar_one():
            raise ProductError(
                f"The product {manifest.key}'s first migration must create "
                f"the schema {manifest.key}."
            )
    with session.transaction() as conn:
        roles = set(conn.execute(text("SELECT key FROM core.role")).scalars())
        for manifest in manifests:
            _register_permissions(conn, manifest, roles)
            _record_installed(conn, manifest)
    return applied


def _migrations(manifest: ProductManifest) -> MigrationSet:
    try:
        return migrations_from_package(manifest.key, manifest.migrations_package)
    except ModuleNotFoundError:
        raise ProductError(
            f"The product {manifest.key}'s migrations package "
            f"{manifest.migrations_package} is missing."
        ) from None


def _register_permissions(
    conn: Connection, manifest: ProductManifest, roles: set[str]
) -> None:
    """Adds new permissions with their default roles, and drops ones the product no
    longer has.

    A permission that is already registered keeps its roles, because the owner
    may have changed them.
    """
    unknown = sorted(
        {role for permission in manifest.permissions for role in permission.roles}
        - roles
    )
    if unknown:
        raise ProductError(
            f"The product {manifest.key}'s permissions name roles that do not exist: "
            f"{', '.join(unknown)}."
        )
    existing: dict[str, str] = {
        key: description
        for key, description in conn.execute(
            text("SELECT key, description FROM core.permission WHERE module = :module"),
            {"module": manifest.key},
        )
    }
    for permission in manifest.permissions:
        if permission.key not in existing:
            conn.execute(
                text(
                    "INSERT INTO core.permission (key, description, module) "
                    "VALUES (:key, :description, :module)"
                ),
                {
                    "key": permission.key,
                    "description": permission.description,
                    "module": manifest.key,
                },
            )
            for role in permission.roles:
                conn.execute(
                    text(
                        "INSERT INTO core.role_permission (role_key, permission_key) "
                        "VALUES (:role, :key)"
                    ),
                    {"role": role, "key": permission.key},
                )
        elif existing[permission.key] != permission.description:
            conn.execute(
                text(
                    "UPDATE core.permission SET description = :description "
                    "WHERE key = :key"
                ),
                {"key": permission.key, "description": permission.description},
            )
    stale = sorted(
        set(existing) - {permission.key for permission in manifest.permissions}
    )
    if stale:
        conn.execute(
            text("DELETE FROM core.permission WHERE key = ANY(:keys)"), {"keys": stale}
        )


def _record_installed(conn: Connection, manifest: ProductManifest) -> None:
    # Only a real change writes the row, so the change history stays free of noise.
    conn.execute(
        text(
            """
            INSERT INTO core.installed_product (key, version, requires)
            VALUES (:key, :version, CAST(:requires AS text[]))
            ON CONFLICT (key) DO UPDATE
               SET version = EXCLUDED.version,
                   requires = EXCLUDED.requires,
                   is_enabled = true
             WHERE (core.installed_product.version,
                    core.installed_product.requires,
                    core.installed_product.is_enabled)
                   IS DISTINCT FROM (EXCLUDED.version, EXCLUDED.requires, true)
            """
        ),
        {
            "key": manifest.key,
            "version": manifest.version,
            "requires": list(manifest.requires),
        },
    )


def _listed(keys: Sequence[str]) -> str:
    if len(keys) == 1:
        return keys[0]
    return f"{', '.join(keys[:-1])} and {keys[-1]}"
