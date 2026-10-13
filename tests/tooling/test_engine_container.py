"""Checks that the engine container matches the Platform Build Plan, task 0.2.

These tests run inside the `engine` container, through `make test`.
"""

import shutil
import sys


def test_python_is_3_13() -> None:
    """The engine runs on Python 3.13, the version the Build Plan sets."""
    assert sys.version_info[:2] == (3, 13)


def test_git_is_not_installed() -> None:
    """Nothing run in the container can write the repository's history."""
    assert shutil.which("git") is None
