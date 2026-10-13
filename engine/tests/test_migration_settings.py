"""Tests for the migration time limit in bowerstone_engine.config.MigrationSettings."""

import os

import pytest
from pydantic import ValidationError

from bowerstone_engine.config import MigrationSettings

OWNER_URL = "postgresql+psycopg://bowerstone_owner:made-up-marker@db:5432/bowerstone"


@pytest.fixture(autouse=True)
def owner_environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Start each test with only the owner's address among BOWERSTONE_ variables."""
    for name in list(os.environ):
        if name.upper().startswith("BOWERSTONE_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("BOWERSTONE_OWNER_DATABASE_URL", OWNER_URL)
    return monkeypatch


def test_migrations_get_thirty_minutes_by_default() -> None:
    """A migration statement may run for 30 minutes, unless set otherwise."""
    assert MigrationSettings().migration_statement_timeout_ms == 1_800_000


@pytest.mark.parametrize(("value", "expected"), [("1", 1), ("86400000", 86_400_000)])
def test_the_migration_time_limit_can_be_set_within_bounds(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: int
) -> None:
    """The limit accepts anything from 1 ms to 24 hours."""
    monkeypatch.setenv("BOWERSTONE_MIGRATION_STATEMENT_TIMEOUT_MS", value)
    assert MigrationSettings().migration_statement_timeout_ms == expected


@pytest.mark.parametrize("value", ["0", "-1", "86400001", "1.5", "half an hour", ""])
def test_a_migration_time_limit_out_of_bounds_is_refused(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Zero, negative, over 24 hours, fractional, and text values are refused."""
    monkeypatch.setenv("BOWERSTONE_MIGRATION_STATEMENT_TIMEOUT_MS", value)
    with pytest.raises(ValidationError):
        MigrationSettings()


def test_the_migration_limits_use_one_connection_and_the_long_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A migration run's connection gets the migration timeout, not the API's."""
    monkeypatch.setenv("BOWERSTONE_STATEMENT_TIMEOUT_MS", "2000")
    monkeypatch.setenv("BOWERSTONE_CONNECT_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("BOWERSTONE_POOL_TIMEOUT_SECONDS", "9")
    settings = MigrationSettings()
    limits = settings.migration_limits()
    assert settings.statement_timeout_ms == 2_000
    assert (
        limits.statement_timeout_ms,
        limits.connect_timeout_seconds,
        limits.pool_size,
        limits.pool_timeout_seconds,
    ) == (1_800_000, 7, 1, 9)
