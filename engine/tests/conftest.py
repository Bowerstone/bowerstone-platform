"""Fixtures for the engine's tests.

The tests run on their own Postgres servers, never on the development database.
`make test` starts them and sets their admin addresses. The Postgres 18 server
comes from the testing kit, bowerstone_engine.testing, which pytest loads as a
plugin, the same way it does in every product repository.
"""

import os
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest
from sqlalchemy import Engine

from bowerstone_engine.db import OWNER_ROLE
from bowerstone_engine.migrate import core_migrations, upgrade
from bowerstone_engine.testing import MakePackage, PostgresServer, ScratchDatabase

PG17_ADMIN_URL_VARIABLE = "BOWERSTONE_TEST_PG17_ADMIN_URL"


@pytest.fixture(scope="session")
def server(bowerstone_server: PostgresServer) -> PostgresServer:
    """The Postgres 18 test server, from the testing kit."""
    return bowerstone_server


@pytest.fixture(scope="session")
def server_pg17() -> PostgresServer:
    """A Postgres 17 test server, for the version check."""
    url = os.environ.get(PG17_ADMIN_URL_VARIABLE)
    if not url:
        pytest.fail(
            f"{PG17_ADMIN_URL_VARIABLE} is not set. Run the tests with `make test`, "
            "which sets it.",
            pytrace=False,
        )
    return PostgresServer.prepare(url)


@pytest.fixture
def database(server: PostgresServer) -> Iterator[ScratchDatabase]:
    """A fresh database on the Postgres 18 test server, dropped after the test."""
    with server.scratch_database() as scratch:
        yield scratch


@pytest.fixture
def owner(database: ScratchDatabase) -> Iterator[Engine]:
    """An engine for `database` as the owner role, which migrations run as."""
    engine = database.engine(OWNER_ROLE)
    yield engine
    engine.dispose()


@pytest.fixture
def core_db(database: ScratchDatabase) -> ScratchDatabase:
    """A fresh database with the core migrations applied."""
    engine = database.engine(OWNER_ROLE)
    try:
        upgrade(engine, [core_migrations()])
    finally:
        engine.dispose()
    return database


@pytest.fixture
def migrations_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MakePackage:
    """Makes importable packages of migration files, for test-only manifests."""

    def make(name: str, files: Mapping[str, str]) -> str:
        package = tmp_path / name
        package.mkdir()
        (package / "__init__.py").write_text("")
        for file_name, content in files.items():
            (package / file_name).write_bytes(content.encode())
        monkeypatch.syspath_prepend(str(tmp_path))
        return name

    return make
