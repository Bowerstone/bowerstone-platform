"""The import check, on the example products and on fixtures that break each rule.

Each fixture is a product package named bowerstone_fixture, with the key
fixture, whose manifest requires notes but not tasks.
"""

from pathlib import Path

import pytest

from bowerstone_engine.importcheck import ImportProblem, check_folders, check_imports
from bowerstone_engine.products import ProductError, product_packages

PACKAGE = "bowerstone_fixture"
PRODUCTS = {
    "notes": "bowerstone_example_notes",
    "tasks": "bowerstone_example_tasks",
    "fixture": PACKAGE,
}
ENGINE_PUBLIC = "auth, core.public, deps, errors, products, types"


def check(tmp_path: Path, files: dict[str, str]) -> list[str]:
    """Writes the fixture package's files, checks them, and returns the problems."""
    folder = tmp_path / PACKAGE
    for name, source in {"__init__.py": "", **files}.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    problems = check_folders(
        [folder],
        package=PACKAGE,
        key="fixture",
        requires=("notes",),
        product_packages=PRODUCTS,
    )
    return [str(problem) for problem in problems]


# The example products, through their entry points.


@pytest.mark.parametrize(
    "package", ["bowerstone_example_notes", "bowerstone_example_tasks"]
)
def test_the_example_products_pass(package: str) -> None:
    assert check_imports(package) == []


def test_each_products_package_comes_from_its_entry_point() -> None:
    assert product_packages() == {
        "notes": "bowerstone_example_notes",
        "tasks": "bowerstone_example_tasks",
    }


def test_a_package_that_is_not_a_product_is_refused() -> None:
    with pytest.raises(ProductError, match="not the package of any installed product"):
        check_imports("sqlalchemy")


# What a product may import.


@pytest.mark.parametrize(
    "source",
    [
        "from bowerstone_engine.types import Money",
        "from bowerstone_engine import auth, deps, errors, products, types",
        "import bowerstone_engine.errors",
        "import bowerstone_engine.core.public as core",
        "from bowerstone_engine.core import public",
        "from bowerstone_engine.core.public import emit_event",
        "from bowerstone_engine.types import *",
        "from bowerstone_example_notes.public import note_exists",
        "from bowerstone_example_notes import public",
        "import bowerstone_example_notes.public",
        "from bowerstone_fixture.service import post",
        "from . import service",
        "from .service import post",
        "import fastapi\nfrom sqlalchemy import text\nimport json",
        "import importlib\nimportlib.import_module('json')",
    ],
)
def test_allowed_imports_pass(tmp_path: Path, source: str) -> None:
    assert check(tmp_path, {"routes.py": source + "\n"}) == []


# Rule 1: only the engine's public modules.


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("from bowerstone_engine.db import set_current_user", "bowerstone_engine.db"),
        ("import bowerstone_engine.migrate", "bowerstone_engine.migrate"),
        ("from bowerstone_engine import installer", "bowerstone_engine.installer"),
        ("import bowerstone_engine", "bowerstone_engine"),
        ("from bowerstone_engine import *", "bowerstone_engine.*"),
        ("from bowerstone_engine.core import routes", "bowerstone_engine.core.routes"),
        (
            "from bowerstone_engine.testing import ScratchDatabase",
            "bowerstone_engine.testing",
        ),
    ],
)
def test_an_engine_module_that_is_not_public_is_refused(
    tmp_path: Path, source: str, target: str
) -> None:
    assert check(tmp_path, {"routes.py": source + "\n"}) == [
        f"bowerstone_fixture/routes.py:1: imports {target}, which is not one of the "
        f"engine's public modules: {ENGINE_PUBLIC}."
    ]


def test_each_name_from_the_engine_package_is_judged_on_its_own(tmp_path: Path) -> None:
    assert check(
        tmp_path, {"routes.py": "from bowerstone_engine import types, db, main\n"}
    ) == [
        "bowerstone_fixture/routes.py:1: imports bowerstone_engine.db, which "
        "is not one "
        f"of the engine's public modules: {ENGINE_PUBLIC}.",
        f"bowerstone_fixture/routes.py:1: imports bowerstone_engine.main, which is not "
        f"one of the engine's public modules: {ENGINE_PUBLIC}.",
    ]


# Rule 2: another product only through its public module, and only if required.


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (
            "from bowerstone_example_notes.routes import Note",
            "bowerstone_example_notes.routes",
        ),
        (
            "from bowerstone_example_notes import routes",
            "bowerstone_example_notes.routes",
        ),
        ("import bowerstone_example_notes", "bowerstone_example_notes"),
        (
            "import bowerstone_example_notes.manifest",
            "bowerstone_example_notes.manifest",
        ),
    ],
)
def test_another_products_private_modules_are_refused(
    tmp_path: Path, source: str, target: str
) -> None:
    assert check(tmp_path, {"routes.py": source + "\n"}) == [
        f"bowerstone_fixture/routes.py:1: imports {target}. Another product may "
        "be used "
        "only through its public module, bowerstone_example_notes.public."
    ]


def test_a_product_the_manifest_does_not_require_is_refused(tmp_path: Path) -> None:
    source = "from bowerstone_example_tasks.public import add_task\n"

    assert check(tmp_path, {"routes.py": source}) == [
        "bowerstone_fixture/routes.py:1: imports the product tasks "
        "(bowerstone_example_tasks.public), which this product's manifest does not "
        "list in requires."
    ]


def test_a_bowerstone_package_that_is_not_installed_is_refused(tmp_path: Path) -> None:
    assert check(
        tmp_path, {"routes.py": "from bowerstone_crm.public import Party\n"}
    ) == [
        "bowerstone_fixture/routes.py:1: imports bowerstone_crm.public, which "
        "is neither "
        "the engine nor an installed product."
    ]


# Ways around the rules are caught too.


@pytest.mark.parametrize(
    ("source", "line"),
    [
        ("import importlib\nimportlib.import_module('bowerstone_engine.db')", 2),
        (
            "from importlib import "
            "import_module\nimport_module('bowerstone_engine.db')",
            2,
        ),
        ("db = __import__('bowerstone_engine.db')", 1),
        (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from bowerstone_engine.db import set_current_user",
            3,
        ),
        (
            "def later() -> None:\n    from bowerstone_engine.db import "
            "set_current_user",
            2,
        ),
    ],
    ids=[
        "import_module",
        "imported import_module",
        "__import__",
        "TYPE_CHECKING",
        "late",
    ],
)
def test_indirect_and_late_imports_are_caught(
    tmp_path: Path, source: str, line: int
) -> None:
    problems = check(tmp_path, {"routes.py": source + "\n"})

    assert problems == [
        f"bowerstone_fixture/routes.py:{line}: imports bowerstone_engine.db, "
        "which is not "
        f"one of the engine's public modules: {ENGINE_PUBLIC}."
    ]


def test_a_relative_import_may_not_climb_out_of_the_package(tmp_path: Path) -> None:
    problems = check(
        tmp_path,
        {
            "routes.py": "from .. import outside\n",
            "sub/__init__.py": "from .. import routes\n",
            "sub/deep.py": "from ... import outside\n",
        },
    )

    assert problems == [
        "bowerstone_fixture/routes.py:1: has a relative import that climbs out of the "
        "package.",
        "bowerstone_fixture/sub/deep.py:1: has a relative import that climbs "
        "out of the "
        "package.",
    ]


def test_every_file_is_checked_and_problems_name_their_file_and_line(
    tmp_path: Path,
) -> None:
    problems = check(
        tmp_path,
        {
            "service.py": "import json\n\nfrom bowerstone_engine.db import "
            "set_current_user\n",
            "reports/__init__.py": "",
            "reports/ledger.py": "from bowerstone_example_notes.routes import Note\n",
        },
    )

    assert [problem.split(": ")[0] for problem in problems] == [
        "bowerstone_fixture/reports/ledger.py:1",
        "bowerstone_fixture/service.py:3",
    ]


def test_a_file_that_is_not_python_is_reported(tmp_path: Path) -> None:
    [problem] = check(tmp_path, {"broken.py": "def (:\n"})

    assert problem.startswith("bowerstone_fixture/broken.py: cannot be read as Python")


def test_a_problem_reads_as_one_line() -> None:
    assert str(ImportProblem("pkg/a.py:3", "imports x.")) == "pkg/a.py:3: imports x."
