"""Tests for bowerstone_engine.config: settings read from BOWERSTONE_ variables.

Every address here is made up. The password in them is a marker: the tests
check that it never appears in printed settings or in error messages.
"""

import os

import pytest
from pydantic import ValidationError

from bowerstone_engine.config import (
    ApiSettings,
    ConnectionLimits,
    Environment,
    MigrationSettings,
)

HIDDEN_MARKER = "made-up-password-marker"
APP_URL = f"postgresql+psycopg://bowerstone_app:{HIDDEN_MARKER}@db:5432/bowerstone"
OWNER_URL = f"postgresql+psycopg://bowerstone_owner:{HIDDEN_MARKER}@db:5432/bowerstone"
ADMIN_URL = f"postgresql+psycopg://bowerstone_admin:{HIDDEN_MARKER}@db:5432/bowerstone"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Start each test with no BOWERSTONE_ variables, whatever the container sets."""
    for name in list(os.environ):
        if name.upper().startswith("BOWERSTONE_"):
            monkeypatch.delenv(name)
    return monkeypatch


def set_api_environment(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    """Set a working API environment, then apply the given changes.

    Args:
        monkeypatch: pytest's tool for changing the environment for one test.
        values: Variables to set, without the BOWERSTONE_ prefix. An empty
            string removes the variable.

    Example:
        >>> set_api_environment(monkeypatch, ENV="production")
    """
    settings = {"ENV": "development", "DATABASE_URL": APP_URL, **values}
    for name, value in settings.items():
        if value == "":
            monkeypatch.delenv(f"BOWERSTONE_{name}", raising=False)
        else:
            monkeypatch.setenv(f"BOWERSTONE_{name}", value)


def failing_fields(error: pytest.ExceptionInfo[ValidationError]) -> set[str]:
    """Name the settings a validation error is about.

    Args:
        error: The caught validation error.

    Returns:
        The field names, such as {"database_url"}.

    Example:
        >>> failing_fields(caught)
        {'env'}
    """
    return {str(detail["loc"][0]) for detail in error.value.errors()}


def test_api_settings_read_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment and the app role's address come from BOWERSTONE_ variables."""
    set_api_environment(monkeypatch)
    settings = ApiSettings()
    assert settings.env is Environment.DEVELOPMENT
    assert settings.database_url.get_secret_value() == APP_URL


def test_production_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both run modes are valid values for BOWERSTONE_ENV."""
    set_api_environment(monkeypatch, ENV="production")
    assert ApiSettings().env is Environment.PRODUCTION


@pytest.mark.parametrize(
    ("missing", "field"), [("ENV", "env"), ("DATABASE_URL", "database_url")]
)
def test_api_settings_refuse_a_missing_value(
    monkeypatch: pytest.MonkeyPatch, missing: str, field: str
) -> None:
    """A missing environment or address stops the engine, naming the setting."""
    set_api_environment(monkeypatch, **{missing: ""})
    with pytest.raises(ValidationError) as refused:
        ApiSettings()
    assert failing_fields(refused) == {field}


def test_migration_settings_refuse_a_missing_owner_address() -> None:
    """Migrations cannot run without the owner role's address."""
    with pytest.raises(ValidationError) as refused:
        MigrationSettings()
    assert failing_fields(refused) == {"owner_database_url"}


def test_migration_settings_read_the_owner_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The owner role's address comes from BOWERSTONE_OWNER_DATABASE_URL."""
    monkeypatch.setenv("BOWERSTONE_OWNER_DATABASE_URL", OWNER_URL)
    assert MigrationSettings().owner_database_url.get_secret_value() == OWNER_URL


@pytest.mark.parametrize(
    "value", ["staging", "Production", "DEVELOPMENT", " development"]
)
def test_an_unknown_environment_is_refused(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Only the exact names development and production are accepted."""
    set_api_environment(monkeypatch, ENV=value)
    with pytest.raises(ValidationError) as refused:
        ApiSettings()
    assert failing_fields(refused) == {"env"}


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        (
            f"postgresql://bowerstone_app:{HIDDEN_MARKER}@db/bowerstone",
            "postgresql+psycopg://",
        ),
        (
            f"postgres://bowerstone_app:{HIDDEN_MARKER}@db/bowerstone",
            "postgresql+psycopg://",
        ),
        ("sqlite:///bowerstone.db", "postgresql+psycopg://"),
        (OWNER_URL, "must sign in as bowerstone_app"),
        (ADMIN_URL, "must sign in as bowerstone_app"),
        (f"postgresql+psycopg://bowerstone_app:{HIDDEN_MARKER}@/bowerstone", "a host"),
        (f"postgresql+psycopg://bowerstone_app:{HIDDEN_MARKER}@db", "a database"),
        (f"not an address {HIDDEN_MARKER}", "cannot be read"),
        ("", "cannot be read"),
    ],
)
def test_a_wrong_api_address_is_refused_without_showing_it(
    monkeypatch: pytest.MonkeyPatch, url: str, reason: str
) -> None:
    """A wrong address is refused, and the message never contains the password."""
    monkeypatch.setenv("BOWERSTONE_ENV", "development")
    monkeypatch.setenv("BOWERSTONE_DATABASE_URL", url)
    with pytest.raises(ValidationError) as refused:
        ApiSettings()
    assert failing_fields(refused) == {"database_url"}
    assert reason in str(refused.value)
    assert HIDDEN_MARKER not in str(refused.value)


@pytest.mark.parametrize("url", [APP_URL, ADMIN_URL])
def test_migrations_only_sign_in_as_the_owner(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    """The owner's settings refuse the app role's and the admin role's addresses."""
    monkeypatch.setenv("BOWERSTONE_OWNER_DATABASE_URL", url)
    with pytest.raises(ValidationError) as refused:
        MigrationSettings()
    assert "must sign in as bowerstone_owner" in str(refused.value)
    assert HIDDEN_MARKER not in str(refused.value)


def test_api_settings_never_read_the_owner_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even when the owner's address is set, the API's settings leave it out."""
    set_api_environment(monkeypatch, OWNER_DATABASE_URL=OWNER_URL)
    settings = ApiSettings()
    assert "owner_database_url" not in ApiSettings.model_fields
    assert HIDDEN_MARKER not in repr(settings.model_dump())


def test_printed_settings_hide_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """Printing or logging the settings never shows the password."""
    set_api_environment(monkeypatch)
    settings = ApiSettings()
    assert HIDDEN_MARKER not in repr(settings)
    assert HIDDEN_MARKER not in str(settings)
    assert HIDDEN_MARKER not in str(settings.model_dump())


def test_limits_have_safe_defaults() -> None:
    """With no limit set, each limit takes its standard value."""
    limits = ConnectionLimits()
    assert (
        limits.statement_timeout_ms,
        limits.connect_timeout_seconds,
        limits.pool_size,
        limits.pool_timeout_seconds,
    ) == (10_000, 5, 10, 10)


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("STATEMENT_TIMEOUT_MS", "1", 1),
        ("STATEMENT_TIMEOUT_MS", "3600000", 3_600_000),
        ("CONNECT_TIMEOUT_SECONDS", "2", 2),
        ("CONNECT_TIMEOUT_SECONDS", "300", 300),
        ("POOL_SIZE", "1", 1),
        ("POOL_SIZE", "100", 100),
        ("POOL_TIMEOUT_SECONDS", "1", 1),
        ("POOL_TIMEOUT_SECONDS", "300", 300),
    ],
)
def test_each_limit_can_be_set_up_to_its_bounds(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str, expected: int
) -> None:
    """Every limit accepts its smallest and largest allowed value."""
    monkeypatch.setenv(f"BOWERSTONE_{name}", value)
    assert getattr(ConnectionLimits(), name.lower()) == expected


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("STATEMENT_TIMEOUT_MS", "0"),
        ("STATEMENT_TIMEOUT_MS", "3600001"),
        ("STATEMENT_TIMEOUT_MS", "-5"),
        ("CONNECT_TIMEOUT_SECONDS", "1"),
        ("CONNECT_TIMEOUT_SECONDS", "301"),
        ("POOL_SIZE", "0"),
        ("POOL_SIZE", "101"),
        ("POOL_SIZE", "2.5"),
        ("POOL_SIZE", "ten"),
        ("POOL_SIZE", ""),
        ("POOL_SIZE", "9" * 1_000),
        ("POOL_TIMEOUT_SECONDS", "0"),
        # Ten in Arabic-Indic digits, which Python's int() would accept.
        (
            "POOL_TIMEOUT_SECONDS",
            "\N{ARABIC-INDIC DIGIT ONE}\N{ARABIC-INDIC DIGIT ZERO}",
        ),
    ],
)
def test_a_limit_out_of_bounds_or_not_a_whole_number_is_refused(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    """Zero, negative, too large, fractional, and non-numeric limits are refused."""
    monkeypatch.setenv(f"BOWERSTONE_{name}", value)
    with pytest.raises(ValidationError) as refused:
        ConnectionLimits()
    assert failing_fields(refused) == {name.lower()}


def test_settings_cannot_be_changed_once_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings are read once and then fixed, so no request can change them."""
    set_api_environment(monkeypatch)
    settings = ApiSettings()
    with pytest.raises(ValidationError):
        settings.pool_size = 99  # type: ignore[misc]
