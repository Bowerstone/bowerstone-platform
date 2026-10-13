"""Tests for scripts/check_file_length.py, the 300-line check (CLAUDE.md rule 8)."""

import tempfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

import check_file_length
from check_file_length import LongFile, count_lines, find_long_files, main

LIMIT = check_file_length.LINE_LIMIT
NOTHING_SKIPPED: frozenset[str] = frozenset()


def write_lines(path: Path, count: int, line: str = "x = 1", end: str = "\n") -> Path:
    """Write a file of `count` identical lines, creating its folders.

    Args:
        path: Where to write the file.
        count: How many lines to write.
        line: The text of each line.
        end: What follows the last line: a line ending, or "" for none.

    Returns:
        The path written.

    Example:
        >>> write_lines(tmp_path / "a.py", 2).read_text()
        'x = 1\\nx = 1\\n'
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join([line] * count) + (end if count else "")
    path.write_bytes(text.encode("utf-8"))
    return path


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"", 0),
        (b"\n", 1),
        (b"one", 1),
        (b"one\n", 1),
        (b"one\ntwo", 2),
        (b"one\ntwo\n", 2),
        (b"one\r\ntwo\r\n", 2),
        ("one\N{LINE SEPARATOR}still one\n".encode(), 1),
        (b"\n\n\n", 3),
    ],
)
def test_count_lines(content: bytes, expected: int) -> None:
    """Only line feeds end lines, and a last line without one still counts."""
    assert count_lines(content) == expected


def test_empty_file_passes(tmp_path: Path) -> None:
    """A file with no lines is within the limit."""
    write_lines(tmp_path / "empty.py", 0)
    assert find_long_files(tmp_path, LIMIT, NOTHING_SKIPPED) == []


def test_file_at_the_limit_passes(tmp_path: Path) -> None:
    """A file of exactly 300 lines is allowed."""
    write_lines(tmp_path / "at_limit.py", LIMIT)
    assert find_long_files(tmp_path, LIMIT, NOTHING_SKIPPED) == []


def test_file_one_line_over_the_limit_fails(tmp_path: Path) -> None:
    """A file of 301 lines is reported with its line count."""
    write_lines(tmp_path / "over.py", LIMIT + 1)
    assert find_long_files(tmp_path, LIMIT, NOTHING_SKIPPED) == [
        LongFile("over.py", LIMIT + 1)
    ]


def test_last_line_without_a_line_ending_counts(tmp_path: Path) -> None:
    """Leaving off the final line feed does not hide the 301st line."""
    write_lines(tmp_path / "over.py", LIMIT + 1, end="")
    assert find_long_files(tmp_path, LIMIT, NOTHING_SKIPPED) == [
        LongFile("over.py", LIMIT + 1)
    ]


def test_windows_line_endings_count_once(tmp_path: Path) -> None:
    """A carriage return before each line feed does not double the count."""
    write_lines(tmp_path / "crlf.sql", LIMIT, line="SELECT 1;\r")
    assert find_long_files(tmp_path, LIMIT, NOTHING_SKIPPED) == []


def test_long_unicode_lines_count_as_one_line_each(tmp_path: Path) -> None:
    """Very long lines of multi-byte characters are still one line each."""
    write_lines(tmp_path / "wide.ts", LIMIT, line="// " + "€日本語🙂" * 5_000)
    assert find_long_files(tmp_path, LIMIT, NOTHING_SKIPPED) == []


@pytest.mark.parametrize("suffix", sorted(check_file_length.SOURCE_SUFFIXES))
def test_every_source_type_is_checked(tmp_path: Path, suffix: str) -> None:
    """Code, SQL, and shell scripts are all held to the limit."""
    write_lines(tmp_path / f"long{suffix}", LIMIT + 1)
    assert find_long_files(tmp_path, LIMIT, NOTHING_SKIPPED) == [
        LongFile(f"long{suffix}", LIMIT + 1)
    ]


@pytest.mark.parametrize(
    "name", ["notes.md", "data.json", "compose.yaml", "Makefile", "x.txt"]
)
def test_other_file_types_are_not_checked(tmp_path: Path, name: str) -> None:
    """Documents, data, and configuration files may be any length."""
    write_lines(tmp_path / name, LIMIT * 3)
    assert find_long_files(tmp_path, LIMIT, NOTHING_SKIPPED) == []


@pytest.mark.parametrize("folder", sorted(check_file_length.SKIPPED_FOLDERS))
def test_skipped_folders_are_not_checked_at_any_depth(
    tmp_path: Path, folder: str
) -> None:
    """Dependencies, build output, caches, and .claude/ are skipped at any depth."""
    write_lines(tmp_path / folder / "long.js", LIMIT * 3)
    write_lines(tmp_path / "web" / "ui" / folder / "long.js", LIMIT * 3)
    assert find_long_files(tmp_path, LIMIT, NOTHING_SKIPPED) == []


def test_generated_files_are_skipped_only_at_their_exact_path(tmp_path: Path) -> None:
    """A skipped generated file does not hide a file of the same name elsewhere."""
    write_lines(tmp_path / "web" / "shell" / "src" / "api-types.ts", LIMIT * 3)
    write_lines(tmp_path / "web" / "ui" / "src" / "api-types.ts", LIMIT + 1)
    skipped = frozenset({"web/shell/src/api-types.ts"})
    assert find_long_files(tmp_path, LIMIT, skipped) == [
        LongFile("web/ui/src/api-types.ts", LIMIT + 1)
    ]


def test_many_long_files_are_all_reported_in_a_stable_order(tmp_path: Path) -> None:
    """Every long file is reported: each folder's files by name, then its subfolders."""
    for path in ["z.py", "a/b.sql", "a/a.ts", "m.sh"]:
        write_lines(tmp_path / path, LIMIT + 5)
    write_lines(tmp_path / "a" / "short.py", 10)
    assert [f.path for f in find_long_files(tmp_path, LIMIT, NOTHING_SKIPPED)] == [
        "m.sh",
        "z.py",
        "a/a.ts",
        "a/b.sql",
    ]


@given(line_count=st.integers(min_value=0, max_value=LIMIT * 2))
def test_a_file_is_reported_exactly_when_it_is_over_the_limit(line_count: int) -> None:
    """For any length from 0 to 600 lines, only lengths above 300 are reported."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        write_lines(root / "any.py", line_count)
        reported = find_long_files(root, LIMIT, NOTHING_SKIPPED)
    assert reported == ([LongFile("any.py", line_count)] if line_count > LIMIT else [])


def test_main_passes_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no long files, the check exits 0 and says every file is within the limit."""
    write_lines(tmp_path / "short.py", 10)
    assert main([str(tmp_path)]) == 0
    assert capsys.readouterr().out == f"Every source file is within {LIMIT} lines.\n"


def test_main_fails_and_names_each_long_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With a long file, the check exits 1 and names the file and its line count."""
    write_lines(tmp_path / "engine" / "big.py", LIMIT + 20)
    assert main([str(tmp_path)]) == 1
    assert capsys.readouterr().err == (
        f"engine/big.py: {LIMIT + 20} lines, "
        f"over the {LIMIT}-line limit (CLAUDE.md rule 8)\n"
    )


def test_main_refuses_a_root_that_is_not_a_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A root that does not exist stops the check with status 2 and a clear message."""
    missing = tmp_path / "missing"
    with pytest.raises(SystemExit) as stopped:
        main([str(missing)])
    assert stopped.value.code == 2
    assert f"{missing} is not a folder" in capsys.readouterr().err


def test_main_refuses_a_missing_root_argument(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Running the check without a root stops with status 2, not a guessed folder."""
    with pytest.raises(SystemExit) as stopped:
        main([])
    assert stopped.value.code == 2
    assert "root" in capsys.readouterr().err
