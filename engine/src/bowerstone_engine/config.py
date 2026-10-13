"""The engine's settings, read from BOWERSTONE_ environment variables.

Two models keep the database roles apart. The running API reads ApiSettings,
which holds only the app role's database address. Migrations and the database
check read MigrationSettings, which holds the owner role's address. So the API
never holds the owner's password (Initial Setup Plan, "Database roles").

The environment and the database addresses have no default: when one is
missing, the engine stops with a message naming it. Only the connection limits
have defaults, each a safe standard value that its setting can change.
"""

from enum import StrEnum

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

DATABASE_DRIVER = "postgresql+psycopg"
OWNER_ROLE = "bowerstone_owner"
APP_ROLE = "bowerstone_app"


class Environment(StrEnum):
    """Where the engine runs: on a developer's computer, or for real users."""

    DEVELOPMENT = "development"
    PRODUCTION = "production"


def check_database_url(url: SecretStr, role: str) -> SecretStr:
    """Check that a database address uses psycopg and signs in as the given role.

    The address never appears in an error message, because it holds a password.

    Args:
        url: The database address, kept secret.
        role: The role the address must sign in as.

    Returns:
        The same address, once it has passed.

    Raises:
        ValueError: If the address cannot be read, uses another driver, signs
            in as another role, or leaves out the host or the database.

    Example:
        >>> url = "postgresql+psycopg://bowerstone_app:pw@db/bowerstone"
        >>> check_database_url(SecretStr(url), APP_ROLE)
        SecretStr('**********')
    """
    try:
        parsed = make_url(url.get_secret_value())
    except ArgumentError:
        # The original error quotes the address, password included.
        raise ValueError("the database address cannot be read as a URL") from None
    if parsed.drivername != DATABASE_DRIVER:
        raise ValueError(f"the database address must start with {DATABASE_DRIVER}://")
    if parsed.username != role:
        raise ValueError(
            f"the database address signs in as {parsed.username!r}, "
            f"but it must sign in as {role}"
        )
    if not parsed.host or not parsed.database:
        raise ValueError("the database address must name a host and a database")
    return url


class ConnectionLimits(BaseSettings):
    """Limits on every database connection the engine opens.

    Attributes:
        statement_timeout_ms: The longest any one query may run, in milliseconds.
        connect_timeout_seconds: The longest a new connection may take to open.
            PostgreSQL's client counts anything below 2 seconds as 2.
        pool_size: How many connections the pool holds. It never opens more.
        pool_timeout_seconds: The longest a request waits for a free connection.
    """

    model_config = SettingsConfigDict(
        env_prefix="BOWERSTONE_",
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    statement_timeout_ms: int = Field(default=10_000, ge=1, le=3_600_000)
    connect_timeout_seconds: int = Field(default=5, ge=2, le=300)
    pool_size: int = Field(default=10, ge=1, le=100)
    pool_timeout_seconds: int = Field(default=10, ge=1, le=300)


class ApiSettings(ConnectionLimits):
    """Settings for the running API, which connects only as the app role.

    Attributes:
        env: Where the engine runs.
        database_url: The app role's database address, kept secret.
    """

    env: Environment
    database_url: SecretStr

    @field_validator("database_url")
    @classmethod
    def _signs_in_as_app_role(cls, url: SecretStr) -> SecretStr:
        """Refuse a database address that does not sign in as bowerstone_app.

        Args:
            url: The database address, kept secret.

        Returns:
            The same address, once it has passed.

        Raises:
            ValueError: As check_database_url does.

        Example:
            >>> ApiSettings().database_url
            SecretStr('**********')
        """
        return check_database_url(url, APP_ROLE)


class MigrationSettings(ConnectionLimits):
    """Settings for migrations and the database check, run as the owner role.

    Attributes:
        owner_database_url: The owner role's database address, kept secret.
        migration_statement_timeout_ms: The longest any one statement of a
            migration may run, in milliseconds. A second migration run also
            waits this long, at most, for the first to finish.
    """

    owner_database_url: SecretStr
    migration_statement_timeout_ms: int = Field(default=1_800_000, ge=1, le=86_400_000)

    def migration_limits(self) -> ConnectionLimits:
        """Give the limits for a migration run, which uses one connection.

        Returns:
            These settings' connect and pool timeouts, the migration statement
            timeout, and a pool of one connection.

        Example:
            >>> MigrationSettings().migration_limits().statement_timeout_ms
            1800000
        """
        return ConnectionLimits(
            statement_timeout_ms=self.migration_statement_timeout_ms,
            connect_timeout_seconds=self.connect_timeout_seconds,
            pool_size=1,
            pool_timeout_seconds=self.pool_timeout_seconds,
        )

    @field_validator("owner_database_url")
    @classmethod
    def _signs_in_as_owner_role(cls, url: SecretStr) -> SecretStr:
        """Refuse a database address that does not sign in as bowerstone_owner.

        Args:
            url: The database address, kept secret.

        Returns:
            The same address, once it has passed.

        Raises:
            ValueError: As check_database_url does.

        Example:
            >>> MigrationSettings().owner_database_url
            SecretStr('**********')
        """
        return check_database_url(url, OWNER_ROLE)
