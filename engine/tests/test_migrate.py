"""The migration runner: loading files, upgrading, downgrading, and the lock."""

import hashlib
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import Engine, text

from bowerstone_engine.db import OWNER_ROLE
from bowerstone_engine.migrate import (
    MigrationError,
    MigrationSet,
    count_statements,
    downgrade,
    migrations_from_folder,
    migrations_from_package,
    upgrade,
)
from bowerstone_engine.testing import ScratchDatabase

SCHEMA = (
    "CREATE SCHEMA example;\n"
    "CREATE TABLE example.note (id integer PRIMARY KEY, body text NOT NULL);\n"
    "INSERT INTO example.note VALUES (1, '100% kept; as written');\n"
)
SCHEMA_DOWN = "DROP SCHEMA example CASCADE;\n"
TAG = "ALTER TABLE example.note ADD COLUMN tag text;\n"
TAG_DOWN = "ALTER TABLE example.note DROP COLUMN tag;\n"
INDEX = (
    "-- bowerstone: no-transaction\n"
    "CREATE INDEX CONCURRENTLY note_body ON example.note (body);\n"
)
INDEX_DOWN = (
    "-- bowerstone: no-transaction\nDROP INDEX CONCURRENTLY example.note_body;\n"
)


def write(folder: Path, name: str, content: str | bytes) -> None:
    """Writes a file with exactly these bytes, whatever the computer's line endings."""
    data = content.encode() if isinstance(content, str) else content
    (folder / name).write_bytes(data)


def write_migration(folder: Path, version: int, name: str, up: str, down: str) -> None:
    write(folder, f"{version:04d}_{name}.sql", up)
    write(folder, f"{version:04d}_{name}.down.sql", down)


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """A migrations folder holding example 0001_schema and 0002_tag."""
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    write_migration(migrations, 1, "schema", SCHEMA, SCHEMA_DOWN)
    write_migration(migrations, 2, "tag", TAG, TAG_DOWN)
    return migrations


def load(folder: Path, module: str = "example") -> MigrationSet:
    return migrations_from_folder(module, folder, "migrations")


def names(migrations: Sequence[object]) -> list[str]:
    return [str(migration) for migration in migrations]


def records(database: ScratchDatabase) -> list[tuple[str, int, str, str]]:
    with database.connect(OWNER_ROLE) as conn:
        rows = conn.execute(
            text(
                "SELECT module, version, name, checksum FROM core.applied_migration "
                "ORDER BY module, version"
            )
        )
        return [(row.module, row.version, row.name, row.checksum) for row in rows]


def query(database: ScratchDatabase, sql: str) -> object:
    with database.connect(OWNER_ROLE) as conn:
        return conn.execute(text(sql)).scalar()


def problems_of(action: Callable[[], object]) -> tuple[str, ...]:
    with pytest.raises(MigrationError) as error:
        action()
    return error.value.problems


# Upgrading.


def test_a_fresh_upgrade_applies_every_migration_and_records_it(
    database: ScratchDatabase, owner: Engine, folder: Path
) -> None:
    applied = upgrade(owner, [load(folder)])

    assert names(applied) == ["example 0001_schema", "example 0002_tag"]
    assert records(database) == [
        ("example", 1, "schema", hashlib.sha256(SCHEMA.encode()).hexdigest()),
        ("example", 2, "tag", hashlib.sha256(TAG.encode()).hexdigest()),
    ]
    # The file ran as written: several statements, with its % sign intact.
    assert query(database, "SELECT body FROM example.note") == "100% kept; as written"
    assert query(database, "SELECT count(tag) FROM example.note") == 0


def test_a_second_run_changes_nothing(
    database: ScratchDatabase, owner: Engine, folder: Path
) -> None:
    upgrade(owner, [load(folder)])
    first_times = query(
        database, "SELECT array_agg(applied_at) FROM core.applied_migration"
    )

    assert upgrade(owner, [load(folder)]) == []
    assert query(
        database, "SELECT array_agg(applied_at) FROM core.applied_migration"
    ) == (first_times)


def test_a_new_migration_is_applied_on_the_next_run(
    database: ScratchDatabase, owner: Engine, folder: Path
) -> None:
    upgrade(owner, [load(folder)])
    write_migration(
        folder, 3, "more", "ALTER TABLE example.note ADD COLUMN more text;\n", ""
    )

    assert names(upgrade(owner, [load(folder)])) == ["example 0003_more"]


def test_modules_are_applied_in_the_order_given(
    database: ScratchDatabase, owner: Engine, folder: Path, tmp_path: Path
) -> None:
    second = tmp_path / "second"
    second.mkdir()
    write_migration(
        second, 1, "uses_example", "CREATE TABLE example.other (id int);\n", ""
    )

    applied = upgrade(owner, [load(folder), load(second, "second")])

    assert names(applied) == [
        "example 0001_schema",
        "example 0002_tag",
        "second 0001_uses_example",
    ]


def test_an_edited_migration_is_refused_and_nothing_runs(
    database: ScratchDatabase, owner: Engine, folder: Path
) -> None:
    upgrade(owner, [load(folder)])
    write(folder, "0001_schema.sql", SCHEMA + "-- an edit after release\n")
    write_migration(
        folder, 3, "more", "ALTER TABLE example.note ADD COLUMN more text;\n", ""
    )

    assert problems_of(lambda: upgrade(owner, [load(folder)])) == (
        "migrations/0001_schema.sql changed after it was applied. Released "
        "migrations are never edited: add a new migration instead.",
    )
    assert [version for _module, version, *_rest in records(database)] == [1, 2]


def test_an_applied_migration_whose_file_is_gone_is_refused(
    database: ScratchDatabase, owner: Engine, folder: Path
) -> None:
    upgrade(owner, [load(folder)])
    (folder / "0002_tag.sql").unlink()
    (folder / "0002_tag.down.sql").unlink()

    assert problems_of(lambda: upgrade(owner, [load(folder)])) == (
        "The database has example 0002_tag, "
        "which these migration files do not include.",
    )


def test_a_failed_migration_keeps_nothing_from_it(
    database: ScratchDatabase, owner: Engine, folder: Path
) -> None:
    write(folder, "0002_tag.sql", TAG + "SELECT FROM nowhere_at_all;\n")

    found = problems_of(lambda: upgrade(owner, [load(folder)]))

    assert len(found) == 1
    assert found[0].startswith("migrations/0002_tag.sql failed: ")
    assert found[0].endswith("Nothing from it was kept.")
    assert [version for _module, version, *_rest in records(database)] == [1]
    assert (
        query(
            database,
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = 'example' AND column_name = 'tag'",
        )
        == 0
    )

    write(folder, "0002_tag.sql", TAG)
    assert names(upgrade(owner, [load(folder)])) == ["example 0002_tag"]


def test_the_same_module_may_not_be_given_twice(owner: Engine, folder: Path) -> None:
    with pytest.raises(ValueError, match="only once"):
        upgrade(owner, [load(folder), load(folder)])


# Downgrading.


def test_downgrade_undoes_migrations_newest_first(
    database: ScratchDatabase, owner: Engine, folder: Path
) -> None:
    upgrade(owner, [load(folder)])

    assert names(downgrade(owner, load(folder), to_version=1)) == ["example 0002_tag"]
    assert [version for _module, version, *_rest in records(database)] == [1]
    assert query(database, "SELECT count(*) FROM example.note") == 1

    assert names(downgrade(owner, load(folder))) == ["example 0001_schema"]
    assert records(database) == []
    assert query(database, "SELECT to_regnamespace('example')") is None

    # Upgrading again restores both.
    assert names(upgrade(owner, [load(folder)])) == [
        "example 0001_schema",
        "example 0002_tag",
    ]


def test_downgrade_refuses_when_an_applied_file_changed(
    database: ScratchDatabase, owner: Engine, folder: Path
) -> None:
    upgrade(owner, [load(folder)])
    write(folder, "0002_tag.sql", TAG + "-- edited\n")

    assert problems_of(lambda: downgrade(owner, load(folder))) == (
        "migrations/0002_tag.sql changed after it was applied. Released "
        "migrations are never edited: add a new migration instead.",
    )
    assert len(records(database)) == 2


# Files that run outside a transaction.


def test_a_no_transaction_file_runs_create_index_concurrently(
    database: ScratchDatabase, owner: Engine, folder: Path
) -> None:
    write_migration(folder, 3, "index", INDEX, INDEX_DOWN)

    assert names(upgrade(owner, [load(folder)]))[-1] == "example 0003_index"
    valid = (
        "SELECT indisvalid FROM pg_index "
        "WHERE indexrelid = 'example.note_body'::regclass"
    )
    assert query(database, valid) is True
    assert [version for _module, version, *_rest in records(database)] == [1, 2, 3]

    assert names(downgrade(owner, load(folder), to_version=2)) == ["example 0003_index"]
    assert query(database, "SELECT to_regclass('example.note_body')") is None


def test_a_no_transaction_file_with_two_statements_is_refused(folder: Path) -> None:
    write_migration(
        folder,
        3,
        "index",
        INDEX + "CREATE INDEX CONCURRENTLY note_id ON example.note (id);\n",
        INDEX_DOWN,
    )

    assert problems_of(lambda: load(folder)) == (
        "migrations/0003_index.sql runs outside a transaction, so it must hold "
        "exactly one statement, and it holds 2.",
    )


# Two runs at the same time.


def advisory_locks(database: ScratchDatabase) -> tuple[int, int]:
    """How many advisory locks in this database are held, and how many are awaited."""
    with database.connect(OWNER_ROLE) as conn:
        row = conn.execute(
            text(
                "SELECT count(*) FILTER (WHERE granted),"
                " count(*) FILTER (WHERE NOT granted)"
                " FROM pg_locks WHERE locktype = 'advisory' AND database ="
                " (SELECT oid FROM pg_database WHERE datname = current_database())"
            )
        ).one()
        return row[0], row[1]


def wait_for(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 10
    while not condition():
        if time.monotonic() > deadline:
            pytest.fail("timed out waiting for the other run")
        time.sleep(0.02)


def test_two_runs_at_the_same_time_apply_each_migration_once(
    database: ScratchDatabase, folder: Path
) -> None:
    # The first migration is slow, so the second run is sure to arrive while
    # the first still holds the lock.
    write(folder, "0001_schema.sql", "SELECT pg_sleep(1.5);\n" + SCHEMA)
    migrations = load(folder)
    first_engine, second_engine = (
        database.engine(OWNER_ROLE),
        database.engine(OWNER_ROLE),
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(upgrade, first_engine, [migrations])
            wait_for(lambda: advisory_locks(database) == (1, 0))
            second = pool.submit(upgrade, second_engine, [migrations])
            wait_for(lambda: advisory_locks(database) == (1, 1))

            assert names(first.result(timeout=30)) == [
                "example 0001_schema",
                "example 0002_tag",
            ]
            assert second.result(timeout=30) == []
    finally:
        first_engine.dispose()
        second_engine.dispose()
    assert [version for _module, version, *_rest in records(database)] == [1, 2]
    assert advisory_locks(database) == (0, 0)


# Loading files: every rule, before anything touches the database.


def test_files_load_in_order_with_their_details(folder: Path) -> None:
    write(folder, "__init__.py", "")
    write(folder, "README.md", "not a migration")
    migrations = load(folder)

    assert migrations.module == "example"
    assert [str(ref) for ref in migrations.refs] == [
        "example 0001_schema",
        "example 0002_tag",
    ]
    first = migrations.migrations[0]
    assert first.up.location == "migrations/0001_schema.sql"
    assert first.up.content == SCHEMA.encode()
    assert first.down.content == SCHEMA_DOWN.encode()
    assert first.checksum == hashlib.sha256(SCHEMA.encode()).hexdigest()
    assert not first.up.no_transaction


def test_files_load_from_a_package(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = folder.parent / "example_package"
    package.mkdir()
    write(package, "__init__.py", "")
    folder.rename(package / "migrations")
    write(package / "migrations", "__init__.py", "")
    monkeypatch.syspath_prepend(str(folder.parent))

    migrations = migrations_from_package("example", "example_package.migrations")

    assert (
        migrations.migrations[1].up.location
        == "example_package/migrations/0002_tag.sql"
    )


def test_a_gap_in_the_numbers_is_refused(folder: Path) -> None:
    write_migration(folder, 4, "later", "SELECT 1;\n", "SELECT 1;\n")

    assert problems_of(lambda: load(folder)) == (
        "migrations has no migration numbered 0003. "
        "Numbers start at 0001 and have no gaps.",
    )


def test_numbers_must_start_at_0001(tmp_path: Path) -> None:
    write_migration(tmp_path, 2, "first", "SELECT 1;\n", "SELECT 1;\n")

    assert problems_of(lambda: load(tmp_path)) == (
        "migrations has no migration numbered 0001. "
        "Numbers start at 0001 and have no gaps.",
    )


def test_two_files_with_one_number_are_refused(folder: Path) -> None:
    write_migration(folder, 2, "other", "SELECT 1;\n", "SELECT 1;\n")

    assert problems_of(lambda: load(folder)) == (
        "migrations/0002_tag.down.sql has number 0002, which another file uses.",
        "migrations/0002_tag.sql has number 0002, which another file uses.",
    )


def test_every_migration_needs_its_down_file(folder: Path) -> None:
    (folder / "0002_tag.down.sql").unlink()
    write(folder, "0003_orphan.down.sql", "SELECT 1;\n")

    assert problems_of(lambda: load(folder)) == (
        "migrations/0002_tag.sql has no down file, 0002_tag.down.sql.",
        "migrations/0003_orphan.down.sql has no migration to undo.",
    )


def test_a_badly_named_file_is_refused(folder: Path) -> None:
    write(folder, "0003_Add-Index.sql", "SELECT 1;\n")

    assert problems_of(lambda: load(folder)) == (
        "migrations/0003_Add-Index.sql is not named like 0001_name.sql or "
        "0001_name.down.sql, using lower-case letters, digits, and underscores.",
    )


@pytest.mark.parametrize("name", ["0002_tag.sql", "0002_tag.down.sql"])
def test_a_file_with_windows_line_endings_is_refused_by_name(
    folder: Path, name: str
) -> None:
    write(folder, name, TAG.replace("\n", "\r\n"))

    assert problems_of(lambda: load(folder)) == (
        f"migrations/{name} has Windows line endings. Migration files must use LF "
        "line endings, because the checksum covers their exact bytes.",
    )


def test_a_file_that_is_not_utf8_is_refused(folder: Path) -> None:
    write(folder, "0002_tag.sql", "SELECT 'caf\xe9';\n".encode("latin-1"))

    assert problems_of(lambda: load(folder)) == (
        "migrations/0002_tag.sql is not UTF-8 text.",
    )


def test_a_no_transaction_file_with_no_statement_is_refused(folder: Path) -> None:
    write_migration(
        folder, 3, "empty", "-- bowerstone: no-transaction\n-- nothing\n", ""
    )

    assert problems_of(lambda: load(folder)) == (
        "migrations/0003_empty.sql runs outside a transaction, so it must hold "
        "exactly one statement, and it holds 0.",
    )


# Counting statements.

ONE_STATEMENT_EACH = [
    "SELECT 1",
    "SELECT 'a; b'",
    "SELECT 'it''s; fine'",
    "SELECT E'back\\'slash; quote'",
    'SELECT 1 AS "odd;name"',
    "SELECT $$ ; $$",
    "CREATE FUNCTION f() RETURNS int LANGUAGE sql AS $body$ SELECT 1; SELECT 2; $body$",
    "SELECT 1 /* a; /* nested; */ still a comment; */",
    "SELECT 1 -- a; comment\n",
    "SELECT a$b FROM t",
    "CREATE INDEX CONCURRENTLY i ON t (c)",
]


@pytest.mark.parametrize("sql", ONE_STATEMENT_EACH)
def test_count_statements_ignores_semicolons_in_quotes_and_comments(sql: str) -> None:
    assert count_statements(sql) == 1
    assert count_statements(sql + ";") == 1
    assert count_statements(f"{sql};\n{sql};") == 2


@given(st.lists(st.sampled_from(ONE_STATEMENT_EACH), min_size=1, max_size=8))
def test_count_statements_counts_any_mix(statements: list[str]) -> None:
    assert count_statements(";\n".join(statements) + ";\n") == len(statements)


@pytest.mark.parametrize(
    "sql", ["", ";", " ;; ", "-- only a comment", "/* only */ ;", "-- marker\n;\n"]
)
def test_count_statements_skips_empty_statements(sql: str) -> None:
    assert count_statements(sql) == 0


@pytest.mark.parametrize(
    "sql", ["SELECT 'open", 'SELECT "open', "SELECT $x$ open", "SELECT /* open /* */"]
)
def test_count_statements_refuses_what_never_closes(sql: str) -> None:
    with pytest.raises(ValueError, match="never closes"):
        count_statements(sql)
