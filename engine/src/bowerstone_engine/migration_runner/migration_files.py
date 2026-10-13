"""Reading and checking a module's migration files (Platform Build Plan, task 1.2).

Each module, the core and every app, keeps numbered SQL files in its own
migrations folder. 0001_name.sql runs on upgrade, and 0001_name.down.sql undoes
it. Numbers run 0001, 0002, and so on with no gaps, and every upgrade file has
a down file. Files are UTF-8 with Unix line endings, because the checksum is
taken over their exact bytes. A file whose first line is the no-transaction
marker runs outside a transaction, for CREATE INDEX CONCURRENTLY.
"""

import hashlib
import re
from dataclasses import dataclass
from importlib.resources.abc import Traversable

from bowerstone_engine.errors import MigrationError

MODULE_NAME = re.compile(r"[a-z][a-z0-9_]{1,30}")
FILE_NAME = re.compile(
    r"(?P<version>\d{4})_(?P<name>[a-z][a-z0-9_]*)(?P<down>\.down)?\.sql"
)
NO_TRANSACTION_MARKER = "-- bowerstone: no-transaction"
# A migrations folder is a Python package, so these sit beside the SQL files.
IGNORED_ENTRIES = frozenset({"__init__.py", "__pycache__"})
UTF8_BYTE_ORDER_MARK = b"\xef\xbb\xbf"


@dataclass(frozen=True)
class MigrationScript:
    """One SQL file of a migration.

    Attributes:
        file_name: The file's name, such as 0001_core.sql.
        sql: The file's text.
        no_transaction: True when the first line is the no-transaction marker.
    """

    file_name: str
    sql: str
    no_transaction: bool


@dataclass(frozen=True)
class Migration:
    """One numbered migration: its upgrade file and the down file that undoes it.

    Attributes:
        version: The migration's number, from 1.
        name: The name after the number, such as core.
        checksum: The SHA-256 of the upgrade file's exact bytes, in hex.
        upgrade: The upgrade file.
        downgrade: The down file.
    """

    version: int
    name: str
    checksum: str
    upgrade: MigrationScript
    downgrade: MigrationScript


def check_module_name(module: str) -> None:
    """Refuse a module name that is not lower case letters, digits, and underscores.

    Args:
        module: The module's name, such as core or accounting.

    Raises:
        MigrationError: If the name is not 2 to 31 such characters, starting
            with a letter.

    Example:
        >>> check_module_name("Core")
        Traceback (most recent call last):
        MigrationError: 'Core' is not a valid module name
    """
    if not MODULE_NAME.fullmatch(module):
        raise MigrationError(f"{module!r} is not a valid module name")


def read_script(module: str, file_name: str, content: bytes) -> MigrationScript:
    """Check one migration file's bytes and read its text.

    Args:
        module: The module the file belongs to, for messages.
        file_name: The file's name, for messages.
        content: The file's exact bytes.

    Returns:
        The file's text, and whether it runs outside a transaction.

    Raises:
        MigrationError: If the file starts with a byte-order mark, contains a
            carriage return, is not UTF-8, or holds nothing but white space.

    Example:
        >>> read_script("core", "0001_core.sql", b"CREATE SCHEMA x;\\n").no_transaction
        False
    """
    where = f"{module}: {file_name}"
    if content.startswith(UTF8_BYTE_ORDER_MARK):
        raise MigrationError(
            f"{where} starts with a byte-order mark; save it without one"
        )
    if b"\r" in content:
        raise MigrationError(
            f"{where} has Windows line endings; check it out with Unix line endings"
        )
    try:
        sql = content.decode("utf-8")
    except UnicodeDecodeError:
        raise MigrationError(f"{where} is not UTF-8 text") from None
    if not sql.strip():
        raise MigrationError(f"{where} is empty")
    first_line = sql.split("\n", 1)[0].strip()
    return MigrationScript(file_name, sql, first_line == NO_TRANSACTION_MARKER)


def _collect_files(
    module: str, folder: Traversable
) -> tuple[dict[int, tuple[str, bytes]], dict[int, tuple[str, bytes]]]:
    """Sort a migrations folder's files into upgrade and down files by number.

    Args:
        module: The module the folder belongs to, for messages.
        folder: The migrations folder.

    Returns:
        Two maps from number to (file name, bytes): upgrade files, down files.

    Raises:
        MigrationError: If the folder is missing, holds an unexpected entry,
            or has two files with the same number.

    Example:
        >>> upgrades, downs = _collect_files("core", folder)
    """
    if not folder.is_dir():
        raise MigrationError(f"{module}: the migrations folder {folder} does not exist")
    upgrades: dict[int, tuple[str, bytes]] = {}
    downs: dict[int, tuple[str, bytes]] = {}
    for entry in sorted(folder.iterdir(), key=lambda item: item.name):
        if entry.name in IGNORED_ENTRIES:
            continue
        match = FILE_NAME.fullmatch(entry.name)
        if match is None or not entry.is_file():
            raise MigrationError(
                f"{module}: unexpected entry {entry.name} in migrations"
            )
        files = downs if match["down"] else upgrades
        version = int(match["version"])
        if version == 0:
            raise MigrationError(f"{module}: {entry.name}: numbers start at 0001")
        if version in files:
            raise MigrationError(
                f"{module}: {entry.name} and {files[version][0]} "
                f"share number {version:04d}"
            )
        files[version] = (match["name"], entry.read_bytes())
    return upgrades, downs


def _check_numbering(
    module: str,
    upgrades: dict[int, tuple[str, bytes]],
    downs: dict[int, tuple[str, bytes]],
) -> None:
    """Check that numbers run from 0001 with no gaps, each with a matching down file.

    Args:
        module: The module the files belong to, for messages.
        upgrades: Upgrade files by number, as from _collect_files.
        downs: Down files by number, as from _collect_files.

    Raises:
        MigrationError: Naming the first missing number, or the first upgrade
            or down file without its partner.

    Example:
        >>> _check_numbering("core", {1: ("core", b"...")}, {})
        Traceback (most recent call last):
        MigrationError: core: 0001_core.sql has no down file 0001_core.down.sql
    """
    for expected, version in enumerate(sorted(upgrades), start=1):
        if version != expected:
            raise MigrationError(f"{module}: migration {expected:04d} is missing")
    for version, (name, _) in sorted(upgrades.items()):
        down = downs.get(version)
        if down is None or down[0] != name:
            raise MigrationError(
                f"{module}: {version:04d}_{name}.sql has no down file "
                f"{version:04d}_{name}.down.sql"
            )
    for version, (name, _) in sorted(downs.items()):
        upgrade = upgrades.get(version)
        if upgrade is None or upgrade[0] != name:
            raise MigrationError(
                f"{module}: {version:04d}_{name}.down.sql has no upgrade file"
            )


def read_migrations(module: str, folder: Traversable) -> list[Migration]:
    """Read and check every migration in a module's migrations folder.

    Args:
        module: The module's name, such as core.
        folder: Its migrations folder, such as
            importlib.resources.files("bowerstone_engine.core.migrations").

    Returns:
        The migrations in order, from 0001. Empty if the folder has none.

    Raises:
        MigrationError: If the module name or any file breaks the rules above.

    Example:
        >>> [m.version for m in read_migrations("core", folder)]
        [1, 2]
    """
    check_module_name(module)
    upgrades, downs = _collect_files(module, folder)
    _check_numbering(module, upgrades, downs)
    migrations = []
    for version, (name, content) in sorted(upgrades.items()):
        down_content = downs[version][1]
        migrations.append(
            Migration(
                version=version,
                name=name,
                checksum=hashlib.sha256(content).hexdigest(),
                upgrade=read_script(module, f"{version:04d}_{name}.sql", content),
                downgrade=read_script(
                    module, f"{version:04d}_{name}.down.sql", down_content
                ),
            )
        )
    return migrations
