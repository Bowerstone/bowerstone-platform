"""Fail when any source file in the repository is longer than 300 lines.

CLAUDE.md rule 8 asks for short files with one clear job. `make lint` runs this
check, which lists every source file over the limit with its line count.

Usage:
    python scripts/check_file_length.py <repository root>
"""

import argparse
import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

LINE_LIMIT = 300

SOURCE_SUFFIXES = frozenset(
    {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css", ".sh", ".sql"}
)

# Dependencies, build output, caches, and .claude/, which Claude Code never edits.
SKIPPED_FOLDERS = frozenset(
    {
        ".git",
        ".claude",
        ".venv",
        "node_modules",
        "dist",
        "build",
        "coverage",
        "playwright-report",
        "test-results",
        "__pycache__",
        ".hypothesis",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)

# Generated files, by path from the repository root. They are rebuilt, never
# edited. Task 2.2 adds the API types generated from the OpenAPI schema.
GENERATED_FILES: frozenset[str] = frozenset()


@dataclass(frozen=True)
class LongFile:
    """A source file over the line limit.

    Attributes:
        path: The file's path from the repository root, with forward slashes.
        line_count: How many lines the file has.
    """

    path: str
    line_count: int


def count_lines(content: bytes) -> int:
    """Count a file's lines the way an editor numbers them.

    Only a line feed ends a line, so Windows line endings count once and
    Unicode line separators inside a line do not start a new one.

    Args:
        content: The file's raw bytes.

    Returns:
        The number of lines. A last line without a line feed still counts.

    Example:
        >>> count_lines(b"first\\nsecond")
        2
    """
    if not content:
        return 0
    line_feeds = content.count(b"\n")
    return line_feeds if content.endswith(b"\n") else line_feeds + 1


def _raise_walk_error(error: OSError) -> None:
    """Stop the walk on a folder that cannot be read, instead of skipping it.

    Args:
        error: The error os.walk met.

    Raises:
        OSError: Always, the error it was given.

    Example:
        >>> _raise_walk_error(PermissionError("cannot read web/ui"))
        Traceback (most recent call last):
        PermissionError: cannot read web/ui
    """
    raise error


def source_files(root: Path, skipped_files: frozenset[str]) -> Iterator[Path]:
    """List the source files under a folder, in a stable order.

    Args:
        root: The repository root.
        skipped_files: Paths from the root, with forward slashes, to leave out.

    Returns:
        An iterator over each source file outside the skipped folders.

    Raises:
        OSError: If a folder under root cannot be read.

    Example:
        >>> [p.name for p in source_files(Path("scripts"), frozenset())]
        ['check_file_length.py']
    """
    for folder, subfolders, file_names in os.walk(root, onerror=_raise_walk_error):
        subfolders[:] = sorted(
            name for name in subfolders if name not in SKIPPED_FOLDERS
        )
        for file_name in sorted(file_names):
            path = Path(folder, file_name)
            if path.suffix not in SOURCE_SUFFIXES:
                continue
            if path.relative_to(root).as_posix() in skipped_files:
                continue
            yield path


def find_long_files(
    root: Path, limit: int, skipped_files: frozenset[str]
) -> list[LongFile]:
    """Find every source file with more lines than the limit.

    Args:
        root: The repository root.
        limit: The most lines a file may have.
        skipped_files: Paths from the root, with forward slashes, to leave out.

    Returns:
        The files over the limit, in a stable order. Empty when none are.

    Raises:
        OSError: If a folder or file under root cannot be read.

    Example:
        >>> find_long_files(Path("."), 300, frozenset())
        []
    """
    long_files = []
    for path in source_files(root, skipped_files):
        line_count = count_lines(path.read_bytes())
        if line_count > limit:
            long_files.append(LongFile(path.relative_to(root).as_posix(), line_count))
    return long_files


def main(arguments: list[str]) -> int:
    """Run the check from the command line and report the result.

    Args:
        arguments: The command-line arguments, without the program name.

    Returns:
        0 when every source file is within the limit, 1 when any is over.

    Raises:
        SystemExit: With status 2 if the root is missing or is not a folder.

    Example:
        >>> main(["."])
        Every source file is within 300 lines.
        0
    """
    parser = argparse.ArgumentParser(
        description=f"Fail when any source file is longer than {LINE_LIMIT} lines."
    )
    parser.add_argument("root", type=Path, help="the repository root to check")
    root: Path = parser.parse_args(arguments).root
    if not root.is_dir():
        parser.error(f"{root} is not a folder")

    long_files = find_long_files(root, LINE_LIMIT, GENERATED_FILES)
    for long_file in long_files:
        print(
            f"{long_file.path}: {long_file.line_count} lines, over the "
            f"{LINE_LIMIT}-line limit (CLAUDE.md rule 8)",
            file=sys.stderr,
        )
    if long_files:
        return 1
    print(f"Every source file is within {LINE_LIMIT} lines.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
