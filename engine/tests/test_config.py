"""Settings come from BOWERSTONE_ environment variables, and never show a password."""

import os
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from bowerstone_engine.config import (
    Settings,
    SettingsError,
    load_settings,
    postgres_url,
)

LEAK_MARKER = "hunter2-not-for-logs"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with no BOWERSTONE_ settings except the ones it sets."""
    for name in list(os.environ):
        if name.upper().startswith("BOWERSTONE_"):
            monkeypatch.delenv(name)


def test_the_defaults() -> None:
    settings = load_settings()
    assert settings.env == "production"
    assert settings.mode == "server"
    assert settings.database_url is None
    assert settings.owner_database_url is None
    assert settings.admin_database_url is None
    assert settings.static_dir is None
    assert settings.desktop_token is None
    assert settings.max_body_bytes == 1_048_576
    assert settings.statement_timeout_ms == 30_000
    assert settings.migration_timeout_ms == 3_600_000
    assert settings.pool_size == 5
    assert settings.pool_timeout_s == 10.0


def test_every_setting_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "ENV": "development",
        "MODE": "desktop",
        "DATABASE_URL": f"postgresql://bowerstone_app:{LEAK_MARKER}@db:5432/bowerstone",
        "OWNER_DATABASE_URL": f"postgresql+psycopg://bowerstone_owner:{LEAK_MARKER}@db/bowerstone",
        "ADMIN_DATABASE_URL": f"postgresql://admin:{LEAK_MARKER}@db/postgres",
        "STATIC_DIR": "/srv/shell",
        "DESKTOP_TOKEN": LEAK_MARKER * 3,
        "MAX_BODY_BYTES": "2048",
        "STATEMENT_TIMEOUT_MS": "500",
        "MIGRATION_TIMEOUT_MS": "60000",
        "POOL_SIZE": "3",
        "POOL_TIMEOUT_S": "2.5",
    }
    for name, value in values.items():
        monkeypatch.setenv(f"BOWERSTONE_{name}", value)

    settings = load_settings()

    assert settings.env == "development"
    assert settings.mode == "desktop"
    assert settings.database_url == SecretStr(values["DATABASE_URL"])
    assert settings.owner_database_url == SecretStr(values["OWNER_DATABASE_URL"])
    assert settings.admin_database_url == SecretStr(values["ADMIN_DATABASE_URL"])
    assert settings.static_dir == Path("/srv/shell")
    assert settings.desktop_token == SecretStr(values["DESKTOP_TOKEN"])
    assert settings.max_body_bytes == 2048
    assert settings.statement_timeout_ms == 500
    assert settings.migration_timeout_ms == 60_000
    assert settings.pool_size == 3
    assert settings.pool_timeout_s == 2.5


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ENV", "staging"),
        ("MODE", "cloud"),
        ("DATABASE_URL", "mysql://user@db/bowerstone"),
        ("DATABASE_URL", "postgresql://user@db"),
        ("OWNER_DATABASE_URL", "not an address"),
        ("MAX_BODY_BYTES", "0"),
        ("STATEMENT_TIMEOUT_MS", "0"),
        ("MIGRATION_TIMEOUT_MS", "0"),
        ("ADMIN_DATABASE_URL", "mysql://admin@db/postgres"),
        ("POOL_SIZE", "0"),
        ("POOL_SIZE", "many"),
        ("POOL_TIMEOUT_S", "0"),
    ],
)
def test_a_bad_value_is_refused_by_name(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(f"BOWERSTONE_{name}", value)
    with pytest.raises(SettingsError, match=f"^BOWERSTONE_{name}: "):
        load_settings()


def test_each_bad_value_gets_its_own_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOWERSTONE_ENV", "staging")
    monkeypatch.setenv("BOWERSTONE_POOL_SIZE", "0")
    with pytest.raises(SettingsError) as error:
        load_settings()
    lines = str(error.value).splitlines()
    assert [line.split(":")[0] for line in lines] == [
        "BOWERSTONE_ENV",
        "BOWERSTONE_POOL_SIZE",
    ]


def test_a_password_never_appears_in_a_setting_or_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "BOWERSTONE_DATABASE_URL",
        f"postgresql://bowerstone_app:{LEAK_MARKER}@db/bowerstone",
    )
    settings = load_settings()
    assert LEAK_MARKER not in repr(settings)
    assert LEAK_MARKER not in str(settings)

    monkeypatch.setenv(
        "BOWERSTONE_DATABASE_URL", f"mysql://bowerstone_app:{LEAK_MARKER}@db/bowerstone"
    )
    with pytest.raises(SettingsError) as settings_error:
        load_settings()
    assert LEAK_MARKER not in str(settings_error.value)
    assert settings_error.value.__cause__ is None

    with pytest.raises(ValidationError) as validation_error:
        Settings(
            database_url=SecretStr(
                f"mysql://bowerstone_app:{LEAK_MARKER}@db/bowerstone"
            )
        )
    assert LEAK_MARKER not in str(validation_error.value)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:pw@db:5432/bowerstone",
        "postgresql+psycopg://user:pw@db:5432/bowerstone",
    ],
)
def test_postgres_url_always_uses_psycopg(url: str) -> None:
    parsed = postgres_url(url)
    assert parsed.drivername == "postgresql+psycopg"
    assert (
        parsed.username,
        parsed.password,
        parsed.host,
        parsed.port,
        parsed.database,
    ) == (
        "user",
        "pw",
        "db",
        5432,
        "bowerstone",
    )


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg2://user@db/bowerstone",
        "sqlite:///bowerstone.db",
        "postgresql://user@db",
        "nonsense",
    ],
)
def test_postgres_url_refuses_anything_else(url: str) -> None:
    with pytest.raises(ValueError, match="address"):
        postgres_url(url)
