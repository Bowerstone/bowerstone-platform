"""The database check: refuse a database that Bowerstone was not set up for.

Migrations run it before they change anything (task 1.2). It requires
PostgreSQL 18, the built-in C.UTF-8 locale, both Bowerstone roles without
superuser rights, and a connection acting as bowerstone_owner. It reads the
facts in one query, then reports every problem at once, so a single run shows
everything that needs fixing.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import Connection, text

from bowerstone_engine.config import APP_ROLE, OWNER_ROLE
from bowerstone_engine.errors import DatabaseCheckError

REQUIRED_MAJOR_VERSION = 18
REQUIRED_LOCALE_PROVIDER = "b"
REQUIRED_LOCALE = "C.UTF-8"
LOCALE_PROVIDER_NAMES = {"b": "built-in", "c": "operating system's", "i": "ICU"}

_FACTS_QUERY = text(
    """
    SELECT current_setting('server_version_num')::int AS server_version_num,
           d.datlocprovider AS locale_provider,
           d.datlocale AS locale,
           current_user AS user_name,
           coalesce(
               (SELECT jsonb_object_agg(r.rolname, r.rolsuper)
                  FROM pg_roles r
                 WHERE r.rolname IN (:owner_role, :app_role)),
               '{}'::jsonb
           ) AS superuser_by_role
      FROM pg_database d
     WHERE d.datname = current_database()
    """
)


@dataclass(frozen=True)
class DatabaseFacts:
    """What the database check needs to know about a database and its server.

    Attributes:
        server_version_num: The server's version, such as 180006 for 18.6.
        locale_provider: "b" for built-in, "c" for the operating system's, "i" for ICU.
        locale: The built-in or ICU locale's name. None for the operating system's.
        user_name: The role the connection acts as.
        superuser_by_role: For each Bowerstone role that exists, whether it is
            a superuser.
    """

    server_version_num: int
    locale_provider: str
    locale: str | None
    user_name: str
    superuser_by_role: Mapping[str, bool]


def read_facts(connection: Connection) -> DatabaseFacts:
    """Read the facts the database check needs, in one query.

    Args:
        connection: A connection to the database to check.

    Returns:
        The facts about that database and its server.

    Example:
        >>> read_facts(connection).user_name
        'bowerstone_owner'
    """
    row = connection.execute(
        _FACTS_QUERY, {"owner_role": OWNER_ROLE, "app_role": APP_ROLE}
    ).one()
    return DatabaseFacts(
        server_version_num=row.server_version_num,
        locale_provider=row.locale_provider,
        locale=row.locale,
        user_name=row.user_name,
        superuser_by_role=dict(row.superuser_by_role),
    )


def _describe_locale(facts: DatabaseFacts) -> str:
    """Say which locale a database uses, for a refusal message.

    Args:
        facts: The facts about the database.

    Returns:
        A sentence naming the locale in use and the one required.

    Example:
        >>> _describe_locale(replace(facts, locale_provider="c", locale=None))
        "the database uses the operating system's locale, not the built-in C.UTF-8"
    """
    provider = LOCALE_PROVIDER_NAMES.get(facts.locale_provider, facts.locale_provider)
    in_use = (
        f"{provider} locale {facts.locale}" if facts.locale else f"{provider} locale"
    )
    return f"the database uses the {in_use}, not the built-in {REQUIRED_LOCALE}"


def find_problems(facts: DatabaseFacts) -> list[str]:
    """List every reason a database is not one Bowerstone may run against.

    Args:
        facts: The facts about the database and its server.

    Returns:
        Each problem in plain words, or an empty list when there are none.

    Example:
        >>> find_problems(replace(facts, server_version_num=170004))
        ['the server is PostgreSQL 17, not 18']
    """
    problems = []
    major_version = facts.server_version_num // 10_000
    if major_version != REQUIRED_MAJOR_VERSION:
        problems.append(
            f"the server is PostgreSQL {major_version}, not {REQUIRED_MAJOR_VERSION}"
        )
    if (
        facts.locale_provider != REQUIRED_LOCALE_PROVIDER
        or facts.locale != REQUIRED_LOCALE
    ):
        problems.append(_describe_locale(facts))
    for role in (OWNER_ROLE, APP_ROLE):
        if role not in facts.superuser_by_role:
            problems.append(f"the role {role} does not exist")
        elif facts.superuser_by_role[role]:
            problems.append(
                f"the role {role} is a superuser, "
                "so it could switch off the ledger's safeguards"
            )
    if facts.user_name != OWNER_ROLE:
        problems.append(f"the connection acts as {facts.user_name}, not {OWNER_ROLE}")
    return problems


def check_database(connection: Connection) -> None:
    """Refuse to continue unless the database is one Bowerstone may run against.

    Args:
        connection: A connection to the database, signed in as bowerstone_owner.

    Raises:
        DatabaseCheckError: Naming every problem found, if there are any.

    Example:
        >>> with owner_pool.connect() as connection:
        ...     check_database(connection)
    """
    problems = find_problems(read_facts(connection))
    if problems:
        raise DatabaseCheckError(problems)
