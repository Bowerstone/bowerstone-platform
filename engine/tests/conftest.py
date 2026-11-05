"""Fixtures for the engine's tests.

The tests run on their own Postgres servers, never on the development database.
`make test` starts them and sets their admin addresses.
"""

import os
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest
from sqlalchemy import Engine

from bowerstone_engine.db import OWNER_ROLE
from bowerstone_engine.testing import (
    ADMIN_URL_VARIABLE,
    MakePackage,
    PostgresServer,
    ScratchDatabase,
)

PG17_ADMIN_URL_VARIABLE = "BOWERSTONE_TEST_PG17_ADMIN_URL"


def _prepare(variable: str) -> PostgresServer:
    url = os.environ.get(variable)
    if not url:
        pytest.fail(
            f"{variable} is not set. Run the tests with `make test`, which sets it.",
            pytrace=False,
        )
    return PostgresServer.prepare(url)


@pytest.fixture(scope="session")
def server() -> PostgresServer:
    """The Postgres 18 test server."""
    return _prepare(ADMIN_URL_VARIABLE)


@pytest.fixture(scope="session")
def server_pg17() -> PostgresServer:
    """A Postgres 17 test server, for the version check."""
    return _prepare(PG17_ADMIN_URL_VARIABLE)


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
