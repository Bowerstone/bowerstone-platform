"""Tests for bowerstone_engine.apps.discover_apps: finding app packages by entry point.

Each test writes a made-up installed distribution, with its entry points, into
a temporary folder on sys.path, which is what pip leaves behind on install.
"""

import importlib
import re
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest

from bowerstone_engine.apps import discover_apps
from bowerstone_engine.errors import AppManifestError

MANIFEST_MODULE = '''"""A made-up app's manifest."""
from bowerstone_engine.apps import AppManifest

APP = AppManifest(
    key="found",
    name="Found",
    version="0.1.0",
    migrations_package="{package}.migrations",
    requires=(),
    engine_versions=">=0.0,<0.1",
    ui_versions=">=0.0,<0.1",
    permissions=(),
    data_deletable=True,
)
NOT_A_MANIFEST = 42
'''

Distribute = Callable[..., str]


@pytest.fixture
def distribute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Distribute:
    """Write made-up installed distributions declaring bowerstone.apps entry points."""
    monkeypatch.syspath_prepend(str(tmp_path))

    def write(
        entry_name: str = "found", target: str = "APP", importable: bool = True
    ) -> str:
        """Write one distribution and its package; return the package's name."""
        package = f"bowerstone_test_found_{uuid.uuid4().hex[:8]}"
        info = tmp_path / f"{package}-0.1.0.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {package}\nVersion: 0.1.0\n"
        )
        (info / "entry_points.txt").write_text(
            f"[bowerstone.apps]\n{entry_name} = {package}.manifest:{target}\n"
        )
        if importable:
            (tmp_path / package).mkdir()
            (tmp_path / package / "__init__.py").write_text('"""A made-up app."""\n')
            (tmp_path / package / "manifest.py").write_text(
                MANIFEST_MODULE.format(package=package)
            )
        importlib.invalidate_caches()
        return package

    return write


def test_an_installed_app_is_found_by_its_entry_point(distribute: Distribute) -> None:
    """The manifest named in a package's entry point is returned under its key."""
    package = distribute()
    found = discover_apps()
    assert list(found) == ["found"]
    assert found["found"].migrations_package == f"{package}.migrations"


def test_an_entry_point_that_cannot_load_is_refused(distribute: Distribute) -> None:
    """A package whose manifest module is missing fails loudly, naming the entry point."""
    distribute(importable=False)
    with pytest.raises(AppManifestError, match="could not be loaded"):
        discover_apps()


def test_an_entry_point_that_is_not_a_manifest_is_refused(
    distribute: Distribute,
) -> None:
    """Only an AppManifest may be declared under bowerstone.apps."""
    distribute(target="NOT_A_MANIFEST")
    with pytest.raises(AppManifestError, match="is not an AppManifest"):
        discover_apps()


def test_an_entry_point_must_be_named_after_its_key(distribute: Distribute) -> None:
    """The entry point's name and the manifest's key must agree."""
    distribute(entry_name="other")
    with pytest.raises(
        AppManifestError, match=re.escape("must be named after its key, found")
    ):
        discover_apps()


def test_two_packages_declaring_one_app_are_refused(distribute: Distribute) -> None:
    """Two installed packages cannot both provide the same app."""
    distribute()
    distribute()
    with pytest.raises(
        AppManifestError, match="two installed packages declare the app found"
    ):
        discover_apps()
