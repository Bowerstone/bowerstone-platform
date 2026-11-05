"""Fixtures for the engine's tests.

The tests run on their own Postgres servers, never on the development database.
`make test` starts them and sets their admin addresses.
"""

import os
from collections.abc import Iterator

import pytest

from bowerstone_engine.testing import (
    ADMIN_URL_VARIABLE,
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
