"""Tests for bowerstone_engine.migrations.migration_files: reading migration files."""

import hashlib
import re
from pathlib import Path

import pytest

from bowerstone_engine.errors import MigrationError
from bowerstone_engine.migration_runner.migration_files import (
    NO_TRANSACTION_MARKER,
    check_module_name,
    read_migrations,
)


def write(folder: Path, file_name: str, content: str | bytes) -> None:
    """Write one file into a migrations folder, as UTF-8 when given text.

    Args:
        folder: The migrations folder.
        file_name: The file's name.
        content: The file's text, or its exact bytes.

    Example:
        >>> write(tmp_path, "0001_demo.sql", "CREATE SCHEMA demo;\\n")
    """
    data = content.encode("utf-8") if isinstance(content, str) else content
    (folder / file_name).write_bytes(data)


def add_pair(
    folder: Path,
    version: int,
    name: str,
    upgrade: str | bytes = "SELECT 1;\n",
    down: str | bytes = "SELECT 1;\n",
) -> None:
    """Write a migration's upgrade file and its down file.

    Args:
        folder: The migrations folder.
        version: The migration's number.
        name: The name after the number.
        upgrade: The upgrade file's text or bytes.
        down: The down file's text or bytes.

    Example:
        >>> add_pair(tmp_path, 1, "demo")
    """
    write(folder, f"{version:04d}_{name}.sql", upgrade)
    write(folder, f"{version:04d}_{name}.down.sql", down)


def test_migrations_are_read_in_number_order(tmp_path: Path) -> None:
    """Files are returned from 0001 up, whatever order the folder lists them in."""
    add_pair(tmp_path, 2, "second", upgrade="CREATE TABLE b ();\n")
    add_pair(
        tmp_path, 1, "first", upgrade="CREATE TABLE a ();\n", down="DROP TABLE a;\n"
    )
    migrations = read_migrations("demo", tmp_path)
    assert [(m.version, m.name) for m in migrations] == [(1, "first"), (2, "second")]
    first = migrations[0]
    assert first.upgrade.file_name == "0001_first.sql"
    assert first.upgrade.sql == "CREATE TABLE a ();\n"
    assert first.downgrade.file_name == "0001_first.down.sql"
    assert first.downgrade.sql == "DROP TABLE a;\n"
    assert first.checksum == hashlib.sha256(b"CREATE TABLE a ();\n").hexdigest()
    assert not first.upgrade.no_transaction


def test_an_empty_folder_has_no_migrations(tmp_path: Path) -> None:
    """A module without migrations is allowed; it simply has nothing to apply."""
    assert read_migrations("demo", tmp_path) == []


def test_python_package_files_are_ignored(tmp_path: Path) -> None:
    """A migrations folder is a Python package, so __init__.py and caches are fine."""
    write(tmp_path, "__init__.py", '"""Migrations."""\n')
    (tmp_path / "__pycache__").mkdir()
    write(tmp_path / "__pycache__", "x.cpython-313.pyc", b"\x00")
    add_pair(tmp_path, 1, "demo")
    assert [m.name for m in read_migrations("demo", tmp_path)] == ["demo"]


def test_a_missing_folder_is_refused(tmp_path: Path) -> None:
    """A module that names a migrations folder that is not there fails loudly."""
    with pytest.raises(MigrationError, match="does not exist"):
        read_migrations("demo", tmp_path / "missing")


@pytest.mark.parametrize(
    ("first_lines", "expected"),
    [
        (f"{NO_TRANSACTION_MARKER}\nCREATE INDEX CONCURRENTLY i ON t (x);\n", True),
        (f"{NO_TRANSACTION_MARKER}   \nCREATE INDEX CONCURRENTLY i ON t (x);\n", True),
        (f"-- A comment first.\n{NO_TRANSACTION_MARKER}\nSELECT 1;\n", False),
        ("--bowerstone: no-transaction\nSELECT 1;\n", False),
        ("SELECT 1;\n", False),
    ],
)
def test_only_the_first_line_marks_a_no_transaction_file(
    tmp_path: Path, first_lines: str, expected: bool
) -> None:
    """The marker counts only as the exact first line, for upgrade and down files."""
    add_pair(tmp_path, 1, "demo", upgrade=first_lines, down=first_lines)
    migration = read_migrations("demo", tmp_path)[0]
    assert migration.upgrade.no_transaction is expected
    assert migration.downgrade.no_transaction is expected


@pytest.mark.parametrize(
    "file_name",
    [
        "1_demo.sql",
        "00001_demo.sql",
        "0001-demo.sql",
        "0001_Demo.sql",
        "0001_.sql",
        "0001_1demo.sql",
        "0001_demo.SQL",
        "0001_demo.up.sql",
        "0001_demo.sql.bak",
        "notes.txt",
    ],
)
def test_a_badly_named_file_is_refused(tmp_path: Path, file_name: str) -> None:
    """Every file must be named NNNN_name.sql or NNNN_name.down.sql."""
    add_pair(tmp_path, 1, "demo")
    write(tmp_path, file_name, "SELECT 1;\n")
    with pytest.raises(
        MigrationError, match=re.escape(f"unexpected entry {file_name}")
    ):
        read_migrations("demo", tmp_path)


def test_a_subfolder_is_refused(tmp_path: Path) -> None:
    """Migrations sit directly in the folder, never in subfolders."""
    add_pair(tmp_path, 1, "demo")
    (tmp_path / "0002_later.sql").mkdir()
    with pytest.raises(
        MigrationError, match=re.escape("unexpected entry 0002_later.sql")
    ):
        read_migrations("demo", tmp_path)


def test_number_zero_is_refused(tmp_path: Path) -> None:
    """Numbers start at 0001."""
    add_pair(tmp_path, 0, "demo")
    with pytest.raises(MigrationError, match="numbers start at 0001"):
        read_migrations("demo", tmp_path)


@pytest.mark.parametrize(
    ("versions", "missing"), [([1, 3], "0002"), ([2], "0001"), ([1, 2, 4, 5], "0003")]
)
def test_a_gap_in_the_numbers_is_refused(
    tmp_path: Path, versions: list[int], missing: str
) -> None:
    """Numbers run 0001, 0002, and so on, and the message names the first missing."""
    for version in versions:
        add_pair(tmp_path, version, f"step{version}")
    with pytest.raises(MigrationError, match=f"migration {missing} is missing"):
        read_migrations("demo", tmp_path)


def test_two_files_with_one_number_are_refused(tmp_path: Path) -> None:
    """Each number belongs to one migration."""
    add_pair(tmp_path, 1, "apples")
    add_pair(tmp_path, 1, "pears")
    with pytest.raises(MigrationError, match="share number 0001"):
        read_migrations("demo", tmp_path)


def test_an_upgrade_without_a_down_file_is_refused(tmp_path: Path) -> None:
    """Every migration must be undoable, so its down file is required."""
    write(tmp_path, "0001_demo.sql", "SELECT 1;\n")
    with pytest.raises(
        MigrationError, match=re.escape("0001_demo.sql has no down file")
    ):
        read_migrations("demo", tmp_path)


def test_a_down_file_without_an_upgrade_is_refused(tmp_path: Path) -> None:
    """A down file on its own is a mistake, not a migration."""
    add_pair(tmp_path, 1, "demo")
    write(tmp_path, "0002_extra.down.sql", "SELECT 1;\n")
    with pytest.raises(
        MigrationError, match=re.escape("0002_extra.down.sql has no upgrade file")
    ):
        read_migrations("demo", tmp_path)


def test_a_down_file_with_another_name_is_refused(tmp_path: Path) -> None:
    """The down file must carry the same name as its upgrade file."""
    write(tmp_path, "0001_demo.sql", "SELECT 1;\n")
    write(tmp_path, "0001_other.down.sql", "SELECT 1;\n")
    with pytest.raises(
        MigrationError, match=re.escape("0001_demo.sql has no down file")
    ):
        read_migrations("demo", tmp_path)


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"SELECT 1;\r\nSELECT 2;\r\n", "Windows line endings"),
        (b"SELECT 1;\rSELECT 2;", "Windows line endings"),
        (b"\xef\xbb\xbfSELECT 1;\n", "byte-order mark"),
        (b"SELECT '\xff\xfe';\n", "not UTF-8"),
        (b"", "is empty"),
        (b"  \n\t\n", "is empty"),
    ],
)
@pytest.mark.parametrize("which", ["upgrade", "down"])
def test_a_badly_encoded_file_is_refused_and_named(
    tmp_path: Path, content: bytes, reason: str, which: str
) -> None:
    """Line endings, encoding, and emptiness are checked on both files."""
    if which == "upgrade":
        add_pair(tmp_path, 1, "demo", upgrade=content)
        file_name = "0001_demo.sql"
    else:
        add_pair(tmp_path, 1, "demo", down=content)
        file_name = "0001_demo.down.sql"
    with pytest.raises(MigrationError) as refused:
        read_migrations("demo", tmp_path)
    assert f"demo: {file_name}" in str(refused.value)
    assert reason in str(refused.value)


def test_unicode_text_is_read_and_checksummed_as_bytes(tmp_path: Path) -> None:
    """Accents and emoji are fine, and the checksum covers the exact UTF-8 bytes."""
    text = "-- Café, Straße, 日本, 🙂\nSELECT 'ß';\n"
    add_pair(tmp_path, 1, "demo", upgrade=text)
    migration = read_migrations("demo", tmp_path)[0]
    assert migration.upgrade.sql == text
    assert migration.checksum == hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("module", ["core", "accounting", "ab", "a" * 31, "app_2"])
def test_valid_module_names_are_accepted(module: str) -> None:
    """Module names are 2 to 31 lower case letters, digits, and underscores."""
    check_module_name(module)


@pytest.mark.parametrize(
    "module", ["Core", "c", "core-x", "", "1core", "a" * 32, "core ", "cöre"]
)
def test_invalid_module_names_are_refused(tmp_path: Path, module: str) -> None:
    """A module name with capitals, symbols, or the wrong length is refused."""
    with pytest.raises(MigrationError, match="is not a valid module name"):
        read_migrations(module, tmp_path)
