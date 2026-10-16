"""Putting apps in dependency order, so each runs after the apps it needs (task 1.4)."""

from collections.abc import Iterable, Mapping

from bowerstone_engine.apps import AppManifest
from bowerstone_engine.errors import InstallError


def install_order(
    keys: Iterable[str], available: Mapping[str, AppManifest]
) -> list[str]:
    """List the given apps and every app they need, each after its dependencies.

    The order is the same every time for the same apps: dependencies in the
    order each manifest lists them, and the given apps in the order given.

    Args:
        keys: The apps to install or upgrade.
        available: Every app whose package is installed, by key.

    Returns:
        Each app once, dependencies first.

    Raises:
        InstallError: If an app, or an app it needs, has no package, or apps
            need each other in a loop. The message names the apps.

    Example:
        >>> install_order(["invoicing"], available)
        ['accounting', 'invoicing']
    """
    order: list[str] = []
    finished: set[str] = set()

    def visit(key: str, path: tuple[str, ...]) -> None:
        """Add an app after everything it needs, following the chain in path."""
        if key in finished:
            return
        if key in path:
            loop = " -> ".join((*path[path.index(key) :], key))
            raise InstallError(f"these apps need each other in a loop: {loop}")
        manifest = available.get(key)
        if manifest is None:
            needed_by = f"{path[-1]} needs {key}, but" if path else f"{key}:"
            raise InstallError(f"{needed_by} no installed package provides it")
        for needed in manifest.requires:
            visit(needed, (*path, key))
        finished.add(key)
        order.append(key)

    for key in keys:
        visit(key, ())
    return order
