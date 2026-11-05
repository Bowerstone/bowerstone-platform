"""The engine's settings, read from BOWERSTONE_ environment variables."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

ENV_PREFIX = "BOWERSTONE_"

# Plain `postgresql://` addresses are accepted, and always run on psycopg.
_POSTGRES_DRIVERS = frozenset({"postgresql", "postgresql+psycopg"})
_DRIVER = "postgresql+psycopg"


class SettingsError(Exception):
    """A setting is missing, or has a value the engine cannot use."""


class Settings(BaseSettings):
    """The engine's settings, each read from BOWERSTONE_ and its name in capitals."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        frozen=True,
        # Database addresses hold passwords, so error messages never repeat a value.
        hide_input_in_errors=True,
    )

    # Production is the default, so a missing setting never loosens anything.
    env: Literal["development", "production"] = "production"
    mode: Literal["server", "desktop"] = "server"

    # The app role's address, for the running engine.
    database_url: SecretStr | None = None
    # The owner role's address, for migrations only.
    # The running engine never holds it.
    owner_database_url: SecretStr | None = None
    # An admin's address, for `bowerstone setup-database` only.
    admin_database_url: SecretStr | None = None

    # The built screens the engine serves.
    static_dir: Path | None = None

    # In desktop mode, every request must carry this in X-Bowerstone-Desktop.
    desktop_token: SecretStr | None = None

    max_body_bytes: int = Field(default=1_048_576, ge=1)
    # At least 1, because 0 turns Postgres's timeout off.
    statement_timeout_ms: int = Field(default=30_000, ge=1)
    # The statement timeout for migrations, which may rewrite large tables.
    migration_timeout_ms: int = Field(default=3_600_000, ge=1)

    # The connection pool has a fixed size.
    # A request waits up to pool_timeout_s for a free connection.
    pool_size: int = Field(default=5, ge=1, le=100)
    pool_timeout_s: float = Field(default=10.0, gt=0)

    @field_validator("database_url", "owner_database_url", "admin_database_url")
    @classmethod
    def _must_be_postgres(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            postgres_url(value.get_secret_value())
        return value


def load_settings() -> Settings:
    """Reads the settings from the environment.

    Raises SettingsError with one line per problem, naming each environment
    variable. The message never includes a value, so no password can leak.
    """
    try:
        return Settings()
    except ValidationError as error:
        problems = [
            f"{ENV_PREFIX}{str(detail['loc'][0]).upper()}: {_plain(detail['msg'])}"
            for detail in error.errors(include_input=False, include_url=False)
        ]
        raise SettingsError("\n".join(problems)) from None


def postgres_url(url: str | URL) -> URL:
    """Parses a Postgres address and points it at the psycopg driver.

    Raises ValueError for anything else. The message never includes the address.
    """
    try:
        parsed = make_url(url)
    except ArgumentError:
        raise ValueError("this is not a database address") from None
    if parsed.drivername not in _POSTGRES_DRIVERS:
        raise ValueError("the address must start with postgresql://")
    if not parsed.database:
        raise ValueError("the address must name a database")
    return parsed.set(drivername=_DRIVER)


def _plain(message: str) -> str:
    return message.removeprefix("Value error, ")
