"""The migration runner and the database check.

Migrations. The core and every product keep numbered SQL files in their own
`migrations` package: `0001_name.sql` upgrades, and `0001_name.down.sql` undoes
it. Numbers start at 0001 and have no gaps. Each file runs exactly as stored,
through the driver with no parameters, so it may hold many statements and `%`
stays as written. Applied files are recorded in core.applied_migration with a
checksum of their bytes, and a file that changes after it was applied is
refused. A file whose first line is `-- bowerstone: no-transaction` runs outside
a transaction and may hold only one statement, such as CREATE INDEX
CONCURRENTLY.

The database check has two forms:

- `check_before_migrating`, run as the owner role before migrations.
- `check_before_serving`, run as the app role before the engine serves.

Each raises DatabaseCheckError, listing every problem it found in plain sentences.
"""

import hashlib
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, cast

import psycopg
from sqlalchemy import Connection, Engine, text

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


# The migration runner.

NO_TRANSACTION_MARKER = "-- bowerstone: no-transaction"

CORE_MODULE = "core"
CORE_MIGRATIONS_PACKAGE = "bowerstone_engine.core.migrations"

_UP_FILE = re.compile(r"(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql")
_DOWN_FILE = re.compile(r"(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.down\.sql")

# Any constant works, as long as every run uses the same one: the bytes of "bwmigrat".
_LOCK_KEY = int.from_bytes(b"bwmigrat", "big")


class MigrationError(Exception):
    """A migration file breaks a rule, or applying one failed."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__(" ".join(self.problems))


@dataclass(frozen=True)
class SqlFile:
    """One migration file, exactly as stored."""

    location: str
    content: bytes
    no_transaction: bool


@dataclass(frozen=True)
class Migration:
    """One numbered migration: its upgrade file and its down file."""

    module: str
    version: int
    name: str
    up: SqlFile
    down: SqlFile

    @property
    def ref(self) -> MigrationRef:
        return MigrationRef(self.module, self.version, self.name)

    @property
    def checksum(self) -> str:
        """The SHA-256 of the upgrade file's exact bytes."""
        return hashlib.sha256(self.up.content).hexdigest()

    def __str__(self) -> str:
        return str(self.ref)


@dataclass(frozen=True)
class MigrationSet:
    """Every migration of one module, the core or a product, in order."""

    module: str
    migrations: tuple[Migration, ...]

    @property
    def refs(self) -> list[MigrationRef]:
        return [migration.ref for migration in self.migrations]


def core_migrations() -> MigrationSet:
    """The core's own migrations, which run before any product's."""
    return migrations_from_package(CORE_MODULE, CORE_MIGRATIONS_PACKAGE)


def migrations_from_package(module: str, package: str) -> MigrationSet:
    """Loads `module`'s migrations from the files in `package`.

    `package` is a Python package name, such as "bowerstone_accounting.migrations".
    """
    return migrations_from_folder(
        module, resources.files(package), package.replace(".", "/")
    )


def migrations_from_folder(
    module: str, folder: Traversable | Path, location: str | None = None
) -> MigrationSet:
    """Loads `module`'s migrations from the .sql files in `folder`.

    Raises MigrationError, listing every rule the files break. `location` names
    the folder in those messages, and defaults to its path.
    """
    where = location or str(folder)
    problems: list[str] = []
    ups: dict[int, tuple[str, SqlFile]] = {}
    downs: dict[int, tuple[str, SqlFile]] = {}

    for entry in sorted(folder.iterdir(), key=lambda entry: entry.name):
        if not entry.name.endswith(".sql"):
            continue
        path = f"{where}/{entry.name}"
        if match := _DOWN_FILE.fullmatch(entry.name):
            found = downs
        elif match := _UP_FILE.fullmatch(entry.name):
            found = ups
        else:
            problems.append(
                f"{path} is not named like 0001_name.sql or 0001_name.down.sql, "
                "using lower-case letters, digits, and underscores."
            )
            continue
        version = int(match["version"])
        if version in found:
            problems.append(
                f"{path} has number {version:04d}, which another file uses."
            )
            continue
        sql_file, file_problems = _read_sql_file(path, entry.read_bytes())
        problems.extend(file_problems)
        found[version] = (match["name"], sql_file)

    for position, version in enumerate(sorted(ups), start=1):
        if version != position:
            problems.append(
                f"{where} has no migration numbered {position:04d}. "
                "Numbers start at 0001 and have no gaps."
            )
            break
    for version, (name, up) in sorted(ups.items()):
        down = downs.get(version)
        if down is None:
            problems.append(
                f"{up.location} has no down file, {version:04d}_{name}.down.sql."
            )
        elif down[0] != name:
            problems.append(f"{down[1].location} does not match {up.location}.")
    for version, (_name, down_file) in sorted(downs.items()):
        if version not in ups:
            problems.append(f"{down_file.location} has no migration to undo.")

    if problems:
        raise MigrationError(problems)
    return MigrationSet(
        module,
        tuple(
            Migration(module, version, name, up, downs[version][1])
            for version, (name, up) in sorted(ups.items())
        ),
    )


def upgrade(engine: Engine, sets: Sequence[MigrationSet]) -> list[Migration]:
    """Applies every migration not yet applied, in one locked session.

    `engine` must connect as the owner role. See MigrationSession.upgrade.
    """
    with migration_session(engine) as session:
        return session.upgrade(sets)


def downgrade(
    engine: Engine, migration_set: MigrationSet, *, to_version: int = 0
) -> list[Migration]:
    """Undoes the module's migrations above `to_version`, in one locked session.

    `engine` must connect as the owner role. See MigrationSession.downgrade.
    """
    with migration_session(engine) as session:
        return session.downgrade(migration_set, to_version=to_version)


class MigrationSession:
    """One connection that holds the migration lock. `migration_session` makes one.

    The lock is a session-level advisory lock, held from start to end, so a
    second run at the same time waits, then finds nothing left to do. The
    installer uses one session for a whole install, so its own changes are
    under the same lock as the migrations.
    """

    def __init__(self, conn: Connection, pg: "_Pg") -> None:
        self._conn = conn
        self._pg = pg

    @property
    def connection(self) -> Connection:
        """The session's connection, in autocommit mode: each statement commits."""
        return self._conn

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        """A transaction on the session's connection, committed when the block ends."""
        with self._pg.transaction():
            yield self._conn

    def upgrade(self, sets: Sequence[MigrationSet]) -> list[Migration]:
        """Applies every migration not yet applied, module by module, in order.

        Before it applies anything, it refuses if an applied migration's file
        changed or is missing. Each migration runs in one transaction with its
        record. A no-transaction file runs on its own, and its record is
        written once it succeeds. Returns the migrations applied.
        """
        _refuse_repeated_modules(sets)
        applied = _read_applied(self._pg)
        problems: list[str] = []
        pending: list[Migration] = []
        for migration_set in sets:
            set_problems, set_pending = _compare(
                migration_set, applied.get(migration_set.module, {})
            )
            problems.extend(set_problems)
            pending.extend(set_pending)
        if problems:
            raise MigrationError(problems)
        for migration in pending:
            _apply(self._pg, migration)
        return pending

    def downgrade(
        self, migration_set: MigrationSet, *, to_version: int = 0
    ) -> list[Migration]:
        """Undoes the module's applied migrations above `to_version`, newest first.

        `to_version=0` undoes all of them, which removes the module's data.
        Each down file runs in one transaction with the removal of its record,
        unless it is a no-transaction file. Returns the migrations undone.
        """
        if to_version < 0:
            raise ValueError("to_version must be 0 or more")
        applied = _read_applied(self._pg).get(migration_set.module, {})
        problems, _pending = _compare(migration_set, applied)
        if problems:
            raise MigrationError(problems)
        undo = [
            migration
            for migration in reversed(migration_set.migrations)
            if migration.version in applied and migration.version > to_version
        ]
        for migration in undo:
            _revert(self._pg, migration)
        return undo


@contextmanager
def migration_session(engine: Engine) -> Iterator[MigrationSession]:
    """Takes the migration lock, runs the owner form of the database check, and
    creates the record table if it is missing.

    `engine` must connect as the owner role. If the database is not one that
    migrations may run on, raises DatabaseCheckError before anything changes.
    """
    with _locked(engine) as (conn, pg):
        check_before_migrating(conn)
        create_record_table(conn)
        yield MigrationSession(conn, pg)


def count_statements(sql: str) -> int:
    """Counts the SQL statements in `sql`, which semicolons separate.

    Semicolons inside quoted strings, quoted names, dollar-quoted strings, and
    comments do not count, and neither do empty statements. Raises ValueError
    for a quote or comment that never closes.
    """
    count = 0
    in_statement = False
    position = 0
    while position < len(sql):
        char = sql[position]
        if sql.startswith("--", position):
            newline = sql.find("\n", position)
            position = len(sql) if newline < 0 else newline + 1
        elif sql.startswith("/*", position):
            position = _skip_block_comment(sql, position)
        elif char == ";":
            count += in_statement
            in_statement = False
            position += 1
        elif char.isspace():
            position += 1
        else:
            in_statement = True
            if char == "'":
                backslashes = _is_escape_string(sql, position)
                position = _skip_quoted(sql, position, "'", backslashes=backslashes)
            elif char == '"':
                position = _skip_quoted(sql, position, '"', backslashes=False)
            elif char == "$" and (tag := _dollar_tag(sql, position)):
                close = sql.find(tag, position + len(tag))
                if close < 0:
                    raise ValueError("a dollar-quoted string never closes")
                position = close + len(tag)
            else:
                position += 1
    return count + in_statement


def _read_sql_file(location: str, content: bytes) -> tuple[SqlFile, list[str]]:
    problems = []
    if b"\r" in content:
        problems.append(
            f"{location} has Windows line endings. Migration files must use LF "
            "line endings, because the checksum covers their exact bytes."
        )
    try:
        sql = content.decode("utf-8")
    except UnicodeDecodeError:
        problems.append(f"{location} is not UTF-8 text.")
        sql = ""
    no_transaction = sql.split("\n", 1)[0].rstrip() == NO_TRANSACTION_MARKER
    if no_transaction and not problems:
        try:
            statements = count_statements(sql)
        except ValueError:
            problems.append(f"{location} has a quote or comment that never closes.")
        else:
            if statements != 1:
                problems.append(
                    f"{location} runs outside a transaction, so it must hold "
                    f"exactly one statement, and it holds {statements}."
                )
    return SqlFile(location, content, no_transaction), problems


def _refuse_repeated_modules(sets: Sequence[MigrationSet]) -> None:
    modules = [migration_set.module for migration_set in sets]
    if len(set(modules)) != len(modules):
        raise ValueError("each module's migrations may be given only once")


_Pg = psycopg.Connection[Any]


@contextmanager
def _locked(engine: Engine) -> Iterator[tuple[Connection, _Pg]]:
    """One connection holding the migration lock, in autocommit mode.

    Autocommit lets the run choose: psycopg's transaction() wraps each
    migration in BEGIN and COMMIT, and a no-transaction file runs bare.
    """
    with engine.connect() as conn:
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        pg = cast(_Pg, conn.connection.driver_connection)
        try:
            pg.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
        except psycopg.errors.QueryCanceled:
            raise MigrationError(
                [
                    "Another migration run held the lock for longer than the "
                    "statement timeout. Try again when it has finished."
                ]
            ) from None
        try:
            yield conn, pg
        finally:
            try:
                pg.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
            except psycopg.Error:
                # Closing the session releases the lock.
                conn.invalidate()


def _read_applied(pg: _Pg) -> dict[str, dict[int, tuple[str, str]]]:
    applied: dict[str, dict[int, tuple[str, str]]] = {}
    rows = pg.execute(
        "SELECT module, version, name, checksum FROM core.applied_migration"
    )
    for module, version, name, checksum in rows:
        applied.setdefault(module, {})[version] = (name, checksum)
    return applied


def _compare(
    migration_set: MigrationSet, applied: Mapping[int, tuple[str, str]]
) -> tuple[list[str], list[Migration]]:
    """Checks the applied migrations against the files, and lists the ones to apply."""
    files = {migration.version: migration for migration in migration_set.migrations}
    problems = []
    for version, (name, checksum) in sorted(applied.items()):
        migration = files.get(version)
        if migration is None:
            problems.append(
                f"The database has {migration_set.module} {version:04d}_{name}, "
                "which these migration files do not include."
            )
        elif migration.name != name or migration.checksum != checksum:
            problems.append(
                f"{migration.up.location} changed after it was applied. Released "
                "migrations are never edited: add a new migration instead."
            )
    pending = [m for m in migration_set.migrations if m.version not in applied]
    if pending and applied and pending[0].version < max(applied):
        problems.append(
            f"The database is missing {pending[0]}, which comes before "
            "migrations it already has."
        )
    return problems, pending


def _apply(pg: _Pg, migration: Migration) -> None:
    try:
        if migration.up.no_transaction:
            _run_alone(pg, migration.up)
            _write_record(pg, migration)
        else:
            with pg.transaction():
                _run(pg, migration.up)
                _write_record(pg, migration)
    except psycopg.Error as error:
        raise _failed(migration.up, error) from error


def _revert(pg: _Pg, migration: Migration) -> None:
    try:
        if migration.down.no_transaction:
            _run_alone(pg, migration.down)
            _delete_record(pg, migration)
        else:
            with pg.transaction():
                # The record goes first, in case the down file drops the record table.
                _delete_record(pg, migration)
                _run(pg, migration.down)
    except psycopg.Error as error:
        raise _failed(migration.down, error) from error


def _run(pg: _Pg, sql_file: SqlFile) -> None:
    # With no parameters, psycopg sends the bytes as they are, and Postgres
    # runs every statement in them.
    with pg.cursor() as cursor:
        cursor.execute(sql_file.content)


def _run_alone(pg: _Pg, sql_file: SqlFile) -> None:
    # binary=True selects the extended protocol, which carries one statement,
    # so Postgres itself refuses a second. Loading already counted them.
    with pg.cursor() as cursor:
        cursor.execute(sql_file.content, prepare=False, binary=True)


def _write_record(pg: _Pg, migration: Migration) -> None:
    pg.execute(
        "INSERT INTO core.applied_migration (module, version, name, checksum) "
        "VALUES (%s, %s, %s, %s)",
        (migration.module, migration.version, migration.name, migration.checksum),
    )


def _delete_record(pg: _Pg, migration: Migration) -> None:
    pg.execute(
        "DELETE FROM core.applied_migration WHERE module = %s AND version = %s",
        (migration.module, migration.version),
    )


def _failed(sql_file: SqlFile, error: psycopg.Error) -> MigrationError:
    if sql_file.no_transaction:
        outcome = "It ran outside a transaction, so check what it left behind."
    else:
        outcome = "Nothing from it was kept."
    detail = str(error).strip()
    return MigrationError([f"{sql_file.location} failed: {detail} {outcome}"])


def _is_escape_string(sql: str, quote: int) -> bool:
    """Whether the quote at `quote` opens an E'...' string, where backslashes escape."""
    if quote == 0 or sql[quote - 1] not in "eE":
        return False
    return quote == 1 or not _is_name_char(sql[quote - 2])


def _skip_quoted(sql: str, start: int, quote: str, *, backslashes: bool) -> int:
    position = start + 1
    while position < len(sql):
        char = sql[position]
        escaped = backslashes and char == "\\"
        doubled = char == quote and sql.startswith(quote, position + 1)
        if escaped or doubled:
            position += 2
        elif char == quote:
            return position + 1
        else:
            position += 1
    raise ValueError("a quote never closes")


def _skip_block_comment(sql: str, start: int) -> int:
    # Block comments nest in Postgres.
    depth = 0
    position = start
    while position < len(sql):
        if sql.startswith("/*", position):
            depth += 1
            position += 2
        elif sql.startswith("*/", position):
            depth -= 1
            position += 2
            if depth == 0:
                return position
        else:
            position += 1
    raise ValueError("a comment never closes")


_DOLLAR_TAG = re.compile(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$")


def _dollar_tag(sql: str, position: int) -> str | None:
    """The tag, such as $$ or $body$, if a dollar-quoted string opens at `position`."""
    # A $ right after a name character belongs to the name, as in a$b.
    if position > 0 and _is_name_char(sql[position - 1]):
        return None
    match = _DOLLAR_TAG.match(sql, position)
    return match.group() if match else None


def _is_name_char(char: str) -> bool:
    return char.isalnum() or char == "_"
