"""The database check, and the table that records applied migrations.

The check has two forms:

- `check_before_migrating`, run as the owner role before migrations.
- `check_before_serving`, run as the app role before the engine serves.

Each raises DatabaseCheckError, listing every problem it found in plain sentences.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import Connection, text

from bowerstone_engine.db import APP_ROLE, OWNER_ROLE

POSTGRES_MAJOR = 18
LOCALE_PROVIDER = "b"  # built-in
LOCALE = "C.UTF-8"
RECORD_TABLE = "core.applied_migration"

_PROVIDER_NAMES = {"b": "built-in", "c": "libc", "i": "ICU"}

_RECORD_TABLE_DDL = (
    "CREATE SCHEMA IF NOT EXISTS core",
    """
    CREATE TABLE IF NOT EXISTS core.applied_migration (
        module     text        NOT NULL,
        version    integer     NOT NULL CHECK (version > 0),
        name       text        NOT NULL,
        checksum   text        NOT NULL,
        applied_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (module, version)
    )
    """,
    f"GRANT USAGE ON SCHEMA core TO {APP_ROLE}",
    f"GRANT SELECT ON core.applied_migration TO {APP_ROLE}",
)

_SERVER_VERSION = text("SELECT current_setting('server_version_num')::integer")

_FACTS = text(
    """
    SELECT d.datlocprovider::text              AS provider,
           coalesce(d.datlocale, d.datcollate) AS locale,
           current_user                        AS user_name,
           owner_role.rolsuper                 AS owner_is_superuser,
           app_role.rolsuper                   AS app_is_superuser
      FROM pg_database AS d
      LEFT JOIN pg_roles AS owner_role ON owner_role.rolname = :owner_role
      LEFT JOIN pg_roles AS app_role   ON app_role.rolname   = :app_role
     WHERE d.datname = current_database()
    """
)

# Finds the record table by OID, so a role without access gets an answer, not an error.
_RECORD_TABLE_ACCESS = text(
    """
    SELECT has_schema_privilege(n.oid, 'USAGE') AND has_table_privilege(c.oid, 'SELECT')
      FROM pg_class AS c
      JOIN pg_namespace AS n ON n.oid = c.relnamespace
     WHERE n.nspname = 'core' AND c.relname = 'applied_migration' AND c.relkind = 'r'
    """
)

_APPLIED = text("SELECT module, version FROM core.applied_migration")


class DatabaseCheckError(Exception):
    """The database is not one the engine may migrate or serve."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("The database check failed. " + " ".join(self.problems))


@dataclass(frozen=True, order=True)
class MigrationRef:
    """One migration file, the core's or a product's, by number."""

    module: str
    version: int
    name: str

    def __str__(self) -> str:
        return f"{self.module} {self.version:04d}_{self.name}"


def check_before_migrating(conn: Connection) -> None:
    """Refuses to continue unless migrations may run on this connection.

    The server must be PostgreSQL 18, and the database must use the built-in
    C.UTF-8 locale. Both roles must exist, and neither may be a superuser.
    The connection must be signed in as the owner role.
    """
    facts = _read_facts(conn)
    _raise_if(
        [
            *_locale_problems(facts),
            *_role_problems(facts, (OWNER_ROLE, APP_ROLE)),
            *_user_problems(facts, OWNER_ROLE, "migrations must run as"),
        ]
    )


def check_before_serving(conn: Connection, expected: Iterable[MigrationRef]) -> None:
    """Refuses to start unless the engine may serve on this connection.

    The server must be PostgreSQL 18, and the database must use the built-in
    C.UTF-8 locale. The connection must be signed in as the app role, which may
    not be a superuser. Every migration in `expected` must have been applied.
    """
    facts = _read_facts(conn)
    _raise_if(
        [
            *_locale_problems(facts),
            *_role_problems(facts, (APP_ROLE,)),
            *_user_problems(facts, APP_ROLE, "the engine must serve as"),
            *_missing_migrations(conn, facts, expected),
        ]
    )


def create_record_table(conn: Connection) -> None:
    """Creates the core schema and the record of applied migrations, if missing."""
    for statement in _RECORD_TABLE_DDL:
        conn.execute(text(statement))


@dataclass(frozen=True)
class _Facts:
    provider: str
    locale: str
    user_name: str
    # Each role's rolsuper flag, or None when the role does not exist.
    superuser: Mapping[str, bool | None]


def _read_facts(conn: Connection) -> _Facts:
    """Reads the server version, then the locale, the roles, and the user.

    A server of another version is refused at once, because the catalog
    columns the rest of the check reads differ between versions.
    """
    major = conn.execute(_SERVER_VERSION).scalar_one() // 10_000
    if major != POSTGRES_MAJOR:
        raise DatabaseCheckError(
            [
                f"The database server runs PostgreSQL {major}, "
                f"and Bowerstone needs PostgreSQL {POSTGRES_MAJOR}."
            ]
        )
    row = conn.execute(_FACTS, {"owner_role": OWNER_ROLE, "app_role": APP_ROLE}).one()
    return _Facts(
        provider=row.provider,
        locale=row.locale,
        user_name=row.user_name,
        superuser={OWNER_ROLE: row.owner_is_superuser, APP_ROLE: row.app_is_superuser},
    )


def _locale_problems(facts: _Facts) -> list[str]:
    if facts.provider == LOCALE_PROVIDER and facts.locale == LOCALE:
        return []
    provider = _PROVIDER_NAMES.get(facts.provider, facts.provider)
    return [
        f"The database uses the {provider} locale {facts.locale}, "
        f"and Bowerstone needs the built-in locale {LOCALE}."
    ]


def _role_problems(facts: _Facts, roles: Sequence[str]) -> list[str]:
    problems = []
    for role in roles:
        is_superuser = facts.superuser[role]
        if is_superuser is None:
            problems.append(f"The role {role} does not exist.")
        elif is_superuser:
            problems.append(
                f"The role {role} is a superuser, and Bowerstone's roles must not be."
            )
    return problems


def _user_problems(facts: _Facts, role: str, rule: str) -> list[str]:
    if facts.user_name == role:
        return []
    return [f"The connection is signed in as {facts.user_name}, and {rule} {role}."]


def _missing_migrations(
    conn: Connection, facts: _Facts, expected: Iterable[MigrationRef]
) -> list[str]:
    wanted = sorted(set(expected))
    if not wanted:
        return []
    readable = conn.execute(_RECORD_TABLE_ACCESS).scalar_one_or_none()
    if readable is False:
        return [f"The role {facts.user_name} cannot read {RECORD_TABLE}."]
    # No record table means no migration has ever run.
    applied = (
        {(module, version) for module, version in conn.execute(_APPLIED)}
        if readable
        else set()
    )
    missing = [ref for ref in wanted if (ref.module, ref.version) not in applied]
    if not missing:
        return []
    names = ", ".join(str(ref) for ref in missing)
    subject = "This migration has" if len(missing) == 1 else "These migrations have"
    return [f"{subject} not been applied: {names}. Run bowerstone migrate first."]


def _raise_if(problems: Sequence[str]) -> None:
    if problems:
        raise DatabaseCheckError(problems)
