"""Fixtures for the installer's tests: test-only apps, and a fresh database each.

The test-only apps are real Python packages, written into a temporary folder
on sys.path, with real migration files. The two used most are ledger, whose
data may not be deleted, like Accounting, and billing, which needs ledger,
like Invoicing.
"""

import importlib
import uuid
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import Connection, Engine, Row, text

from bowerstone_engine.apps import AppManifest, Permission

ENGINE_VERSION = "0.0.5"
MakeApp = Callable[..., AppManifest]
Query = Callable[..., Sequence[Row[Any]]]


def default_migrations(key: str) -> dict[str, str]:
    """Give a test app one migration that creates its schema and a table.

    Args:
        key: The app's key, which is also its schema's name.

    Returns:
        Migration file names and their SQL.

    Example:
        >>> sorted(default_migrations("ledger"))
        ['0001_ledger.down.sql', '0001_ledger.sql']
    """
    return {
        f"0001_{key}.sql": (
            f"CREATE SCHEMA {key};\n"
            f"CREATE TABLE {key}.item (id bigint GENERATED ALWAYS AS IDENTITY "
            "PRIMARY KEY, name text NOT NULL);\n"
        ),
        f"0001_{key}.down.sql": f"DROP SCHEMA {key} CASCADE;\n",
    }


def default_permissions(key: str) -> tuple[Permission, ...]:
    """Give a test app a permission everyone holds and one only the owner holds.

    Args:
        key: The app's key.

    Returns:
        The two permissions.

    Example:
        >>> [p.key for p in default_permissions("ledger")]
        ['ledger.view', 'ledger.edit']
    """
    return (
        Permission(f"{key}.view", f"View the {key}", ("owner", "staff", "read_only")),
        Permission(f"{key}.edit", f"Change the {key}", ("owner",)),
    )


@pytest.fixture
def migrations_for() -> Callable[[str], dict[str, str]]:
    """Give a test app's default migrations, exactly as app_factory writes them."""
    return default_migrations


@pytest.fixture
def app_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MakeApp:
    """Make test-only apps as importable packages with migration files."""
    root = tmp_path / "app_packages"
    root.mkdir()
    monkeypatch.syspath_prepend(str(root))
    run = uuid.uuid4().hex[:8]

    def make_app(
        key: str,
        *,
        version: str = "0.1.0",
        requires: tuple[str, ...] = (),
        migrations: dict[str, str] | None = None,
        permissions: tuple[Permission, ...] | None = None,
        data_deletable: bool = True,
        engine_versions: str = ">=0.0,<0.1",
    ) -> AppManifest:
        """Write an app package and return its manifest."""
        package = f"bowerstone_test_{key}_{run}_{version.replace('.', '_')}"
        folder = root / package / "migrations"
        folder.mkdir(parents=True)
        (root / package / "__init__.py").write_text('"""A test-only app."""\n')
        (folder / "__init__.py").write_text('"""Its migrations."""\n')
        for file_name, sql in (migrations or default_migrations(key)).items():
            (folder / file_name).write_bytes(sql.encode("utf-8"))
        importlib.invalidate_caches()
        return AppManifest(
            key=key,
            name=key.title(),
            version=version,
            migrations_package=f"{package}.migrations",
            requires=requires,
            engine_versions=engine_versions,
            ui_versions=">=0.0,<0.1",
            permissions=default_permissions(key)
            if permissions is None
            else permissions,
            data_deletable=data_deletable,
        )

    return make_app


@pytest.fixture
def ledger(app_factory: MakeApp) -> AppManifest:
    """A test app whose data may not be deleted, like Accounting."""
    return app_factory("ledger", data_deletable=False)


@pytest.fixture
def billing(app_factory: MakeApp) -> AppManifest:
    """A test app that needs ledger, like Invoicing needs Accounting."""
    return app_factory("billing", requires=("ledger",))


@pytest.fixture
def owner_pool(
    database_factory: Callable[[str], str],
    owner_url_for: Callable[[str], SecretStr],
    pool_factory: Callable[..., Engine],
) -> Engine:
    """Connections as the owner role to a database made for this test alone."""
    return pool_factory(owner_url_for(database_factory("builtin")), pool_size=3)


@pytest.fixture
def connection(owner_pool: Engine) -> Iterator[Connection]:
    """One connection as the owner role, with no open transaction."""
    with owner_pool.connect() as connection:
        yield connection


@pytest.fixture
def query(owner_pool: Engine) -> Query:
    """Run a read-only query on this test's database, on a connection of its own."""

    def run(sql: str, **values: Any) -> Sequence[Row[Any]]:
        """Return every row the query gives."""
        with owner_pool.connect() as connection:
            return connection.execute(text(sql), values).all()

    return run


@pytest.fixture
def schemas(query: Query) -> Callable[[], set[str]]:
    """Name the core and test-app schemas that exist in this test's database."""
    return lambda: {
        row.nspname
        for row in query(
            "SELECT nspname FROM pg_namespace "
            "WHERE nspname IN ('core', 'ledger', 'billing', 'extra', 'wrong_name')"
        )
    }


@pytest.fixture
def installed(query: Query) -> Callable[[], dict[str, tuple[str, bool]]]:
    """Read core.installed_app as key: (version, enabled)."""
    return lambda: {
        row.key: (row.version, row.is_enabled)
        for row in query("SELECT key, version, is_enabled FROM core.installed_app")
    }
