"""The testing kit, seen from a product repository: pytest runs on a small
throwaway project, in its own process, with the kit loaded as a plugin."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

TEST_FILE = """
from sqlalchemy import text


def test_uses_the_kit(db):
    assert db.execute(text("SELECT count(*) FROM notes.note")).scalar_one() == 0
"""


def run_pytest(
    project: Path, *, product: str | None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Writes a product's test project to `project`, runs pytest on it, and returns
    the result."""
    project.mkdir(exist_ok=True)
    setting = f'bowerstone_product = "{product}"\n' if product is not None else ""
    (project / "pyproject.toml").write_text(
        f"[tool.pytest.ini_options]\n{setting}addopts = ['--strict-config']\n"
    )
    (project / "test_product.py").write_text(TEST_FILE)
    return subprocess.run(  # noqa: S603 - the test's own command
        [sys.executable, "-m", "pytest", str(project)],
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_a_product_names_itself_and_gets_a_database(tmp_path: Path) -> None:
    result = run_pytest(tmp_path / "notes", product="notes")

    assert result.returncode == 0, result.stdout
    assert "1 passed" in result.stdout


@pytest.mark.parametrize(
    ("product", "env", "message"),
    [
        (None, None, "Set bowerstone_product in [tool.pytest.ini_options]"),
        ("invoices", None, "The product invoices's package is not installed"),
        (
            "notes",
            {"BOWERSTONE_TEST_ADMIN_URL": ""},
            "BOWERSTONE_TEST_ADMIN_URL is not set",
        ),
    ],
    ids=["no setting", "unknown product", "no test server"],
)
def test_the_kit_says_what_is_missing(
    tmp_path: Path, product: str | None, env: dict[str, str] | None, message: str
) -> None:
    result = run_pytest(tmp_path / "product", product=product, env=env)

    assert result.returncode == 1
    assert message in result.stdout
    # A plain message, not a trace through the kit's own code.
    assert "testing.py:" not in result.stdout
