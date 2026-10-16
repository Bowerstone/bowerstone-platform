"""Tests for bowerstone_engine.app_dependencies.install_order."""

import re
from dataclasses import replace

import pytest

from bowerstone_engine.app_dependencies import install_order
from bowerstone_engine.apps import AppManifest
from bowerstone_engine.errors import InstallError

BASE = AppManifest(
    key="base",
    name="Base",
    version="0.1.0",
    migrations_package="bowerstone_base.migrations",
    requires=(),
    engine_versions=">=0.0,<0.1",
    ui_versions=">=0.0,<0.1",
    permissions=(),
    data_deletable=True,
)


def apps(**requires: tuple[str, ...]) -> dict[str, AppManifest]:
    """Make manifests that need only the given apps.

    Args:
        requires: Each app's key and the keys it requires.

    Returns:
        The manifests, by key.

    Example:
        >>> sorted(apps(a=(), b=("a",)))
        ['a', 'b']
    """
    return {
        key: replace(BASE, key=key, requires=needs) for key, needs in requires.items()
    }


def test_dependencies_come_first() -> None:
    """Each app follows the apps it needs, however deep the chain."""
    available = apps(ledger=(), billing=("ledger",), reports=("billing", "ledger"))
    assert install_order(["reports"], available) == ["ledger", "billing", "reports"]


def test_a_shared_dependency_appears_once() -> None:
    """Two apps needing the same app put it in the order once."""
    available = apps(ledger=(), billing=("ledger",), payroll=("ledger",))
    assert install_order(["billing", "payroll"], available) == [
        "ledger",
        "billing",
        "payroll",
    ]


def test_the_order_is_the_same_every_time() -> None:
    """The given order and each manifest's requires order decide ties."""
    available = apps(aa=(), bb=(), cc=("bb", "aa"))
    assert install_order(["cc"], available) == ["bb", "aa", "cc"]
    assert install_order(["aa", "bb"], available) == ["aa", "bb"]


def test_an_app_without_a_package_is_refused() -> None:
    """Asking for an app no package provides fails before anything else."""
    with pytest.raises(
        InstallError, match=re.escape("billing: no installed package provides it")
    ):
        install_order(["billing"], apps(ledger=()))


def test_a_missing_dependency_names_both_apps() -> None:
    """The message says which app needs which missing one."""
    with pytest.raises(
        InstallError,
        match=re.escape("billing needs ledger, but no installed package provides it"),
    ):
        install_order(["billing"], apps(billing=("ledger",)))


@pytest.mark.parametrize(
    ("requires", "loop"),
    [
        ({"aa": ("bb",), "bb": ("aa",)}, "aa -> bb -> aa"),
        ({"aa": ("bb",), "bb": ("cc",), "cc": ("aa",)}, "aa -> bb -> cc -> aa"),
        ({"top": ("aa",), "aa": ("bb",), "bb": ("aa",)}, "aa -> bb -> aa"),
    ],
)
def test_a_dependency_loop_is_refused_and_shown(
    requires: dict[str, tuple[str, ...]], loop: str
) -> None:
    """Apps that need each other in a loop are refused, naming the loop."""
    first = next(iter(requires))
    with pytest.raises(
        InstallError, match=re.escape(f"need each other in a loop: {loop}")
    ):
        install_order([first], apps(**requires))
