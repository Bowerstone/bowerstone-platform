"""The import check: a product may use the engine and other products only
through their public modules.

The check reads every .py file of a product's package, without importing or
running any of it, and reports each import that breaks a rule:

- From the engine, a product may import only the engine's public modules, its
  SDK: core.public, types, errors, auth, products, and deps.
- From another product, a product may import only that product's public module,
  and only if its manifest lists that product in `requires`.

Imports of the product's own package, the standard library, and other
libraries are its own business. Every product repository runs this check in
its automated build, through `bowerstone check-imports <package>`.
"""

import ast
import importlib.util
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from bowerstone_engine.products import ProductError, load_products, product_packages

ENGINE_PACKAGE = "bowerstone_engine"
ENGINE_PUBLIC = frozenset(
    f"{ENGINE_PACKAGE}.{name}"
    for name in ("core.public", "types", "errors", "auth", "products", "deps")
)
# Packages that hold public modules, so `from <package> import <public module>` works.
_ENGINE_PUBLIC_PARENTS = frozenset({ENGINE_PACKAGE, f"{ENGINE_PACKAGE}.core"})
_BOWERSTONE_PREFIX = "bowerstone_"
_PUBLIC = "public"


@dataclass(frozen=True, order=True)
class ImportProblem:
    """One import that breaks a rule."""

    # The file and line, such as bowerstone_accounting/service.py:12.
    location: str
    message: str

    def __str__(self) -> str:
        return f"{self.location}: {self.message}"


def check_imports(package: str) -> list[ImportProblem]:
    """Checks the installed product whose top-level package is `package`.

    Raises ProductError if `package` is not the package of a present product.
    """
    packages = product_packages()
    key = next((key for key, name in packages.items() if name == package), None)
    if key is None:
        raise ProductError(f"{package} is not the package of any installed product.")
    manifest = load_products()[key]
    return check_folders(
        _package_folders(package),
        package=package,
        key=key,
        requires=manifest.requires,
        product_packages=packages,
    )


def check_folders(
    folders: Iterable[Path],
    *,
    package: str,
    key: str,
    requires: Iterable[str],
    product_packages: Mapping[str, str],
) -> list[ImportProblem]:
    """Checks every .py file in `folders`, which hold the package `package`.

    `product_packages` gives every product's top-level package by key, so the
    check can tell another product's imports from other libraries'.
    """
    rules = _Rules(package, key, frozenset(requires), dict(product_packages))
    problems: list[ImportProblem] = []
    for folder in folders:
        for path in sorted(folder.rglob("*.py")):
            relative = path.relative_to(folder)
            problems.extend(rules.check_file(path, relative))
    return sorted(problems)


@dataclass(frozen=True)
class _Rules:
    package: str
    key: str
    requires: frozenset[str]
    product_packages: dict[str, str]

    def check_file(self, path: Path, relative: Path) -> Iterator[ImportProblem]:
        shown = f"{self.package}/{relative.as_posix()}"
        try:
            tree = ast.parse(path.read_bytes(), filename=shown)
        except (SyntaxError, ValueError) as error:
            yield ImportProblem(shown, f"cannot be read as Python: {error}")
            return
        parts = [self.package, *relative.with_suffix("").parts]
        if parts[-1] == "__init__":
            parts.pop()
        # The package a relative import starts from.
        home = parts if relative.name == "__init__.py" else parts[:-1]
        for node in ast.walk(tree):
            for message in self._check_node(node, home):
                yield ImportProblem(f"{shown}:{getattr(node, 'lineno', 0)}", message)

    def _check_node(self, node: ast.AST, home: Sequence[str]) -> Iterator[str]:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield from self._check_module(alias.name, ())
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # A relative import stays in the package, unless it climbs out.
                if node.level > len(home):
                    yield "has a relative import that climbs out of the package."
                return
            yield from self._check_module(
                str(node.module), tuple(alias.name for alias in node.names)
            )
        elif isinstance(node, ast.Call):
            target = _dynamic_import(node)
            if target is not None and not target.startswith("."):
                yield from self._check_module(target, ())

    def _check_module(self, module: str, names: Sequence[str]) -> Iterator[str]:
        top = module.split(".")[0]
        if top == self.package:
            return
        if top == ENGINE_PACKAGE:
            yield from self._check_engine(module, names)
            return
        product = next(
            (key for key, package in self.product_packages.items() if package == top),
            None,
        )
        if product is not None:
            yield from self._check_product(product, top, module, names)
        elif top.startswith(_BOWERSTONE_PREFIX):
            yield (
                f"imports {module}, which is neither the engine "
                "nor an installed product."
            )

    def _check_engine(self, module: str, names: Sequence[str]) -> Iterator[str]:
        if module in ENGINE_PUBLIC:
            return
        if module in _ENGINE_PUBLIC_PARENTS and names:
            targets = [f"{module}.{name}" for name in names]
            bad = [target for target in targets if target not in ENGINE_PUBLIC]
        else:
            bad = [module]
        public = ", ".join(
            sorted(name.removeprefix(f"{ENGINE_PACKAGE}.") for name in ENGINE_PUBLIC)
        )
        for target in bad:
            yield (
                f"imports {target}, which is not one of the engine's public modules: "
                f"{public}."
            )

    def _check_product(
        self, product: str, package: str, module: str, names: Sequence[str]
    ) -> Iterator[str]:
        if product == self.key:
            return
        if product not in self.requires:
            yield (
                f"imports the product {product} ({module}), which this product's "
                "manifest does not list in requires."
            )
            return
        public = f"{package}.{_PUBLIC}"
        if module == public or module.startswith(f"{public}."):
            return
        if module == package and names:
            bad = [f"{package}.{name}" for name in names if name != _PUBLIC]
        else:
            bad = [module]
        for target in bad:
            yield (
                f"imports {target}. Another product may be used only through its "
                f"public module, {public}."
            )


def _dynamic_import(node: ast.Call) -> str | None:
    """The module a call to import_module or __import__ names, if it is a literal."""
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name not in ("import_module", "__import__") or not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _package_folders(package: str) -> list[Path]:
    spec = importlib.util.find_spec(package)
    if spec is None:
        raise ProductError(f"The package {package} is not installed.")
    if spec.submodule_search_locations:
        return [Path(location) for location in spec.submodule_search_locations]
    raise ProductError(
        f"{package} is a single module, and a product must be a package."
    )
