"""The `bowerstone` command, which also runs as `python -m bowerstone_engine`.

The desktop app, Docker, and the hosted version all run the engine through
these commands. The desktop app always uses `python -m bowerstone_engine`,
because the `bowerstone` launcher records the full path of the Python it was
installed with, and breaks when the portable Python folder moves.

Every error prints one plain sentence and exits with status 1. None prints a
stack trace.
"""

import argparse
import copy
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any, NoReturn

import psycopg
from sqlalchemy import Engine
from sqlalchemy.exc import DBAPIError

from bowerstone_engine import db, installer
from bowerstone_engine.config import Settings, SettingsError, load_settings
from bowerstone_engine.importcheck import check_imports
from bowerstone_engine.migrate import DatabaseCheckError, MigrationError
from bowerstone_engine.products import ProductError, load_products

PROG = "bowerstone"
TAKES_EFFECT = "This takes effect when the engine next starts."


def main(argv: Sequence[str] | None = None) -> int:
    """Runs one command, and returns its exit status."""
    args = _parser().parse_args(argv)
    try:
        status: int = args.run(args)
    except (SettingsError, DatabaseCheckError, MigrationError, ProductError) as error:
        return _fail(str(error))
    except DBAPIError as error:
        return _fail(_database_problem(error.orig))
    except psycopg.Error as error:
        return _fail(_database_problem(error))
    except KeyboardInterrupt:
        return 130
    except Exception as error:  # Anything else still gets one sentence, not a trace.
        return _fail(
            f"An unexpected error stopped the command: {type(error).__name__}: {error}"
        )
    return status


def _setup_database(args: argparse.Namespace) -> int:
    for step in db.setup_database(load_settings(), args.admin_url):
        print(step)
    print("The database is ready for bowerstone migrate.")
    return 0


def _migrate(args: argparse.Namespace) -> int:
    with _engine(load_settings(), owner=True) as engine:
        applied = installer.upgrade_all(engine)
    for migration in applied:
        print(f"Applied {migration}.")
    print("The database is up to date.")
    return 0


def _products_list(args: argparse.Namespace) -> int:
    catalog = load_products()
    settings = load_settings()
    reader = _engine(settings, owner=settings.database_url is None)
    with reader as engine, engine.connect() as conn:
        installed = installer.installed_products(conn)
    keys = sorted(set(catalog) | set(installed))
    if not keys:
        print("No products are present.")
        return 0
    lines = [("KEY", "NAME", "VERSION", "STATE")]
    for key in keys:
        manifest = catalog.get(key)
        record = installed.get(key)
        if record is None:
            state = "available"
        elif manifest is None:
            state = (
                "installed" if record.is_enabled else "removed"
            ) + ", package missing"
        elif not record.is_enabled:
            state = "removed"
        elif record.version != manifest.version:
            state = f"installed {record.version}: run bowerstone migrate"
        else:
            state = "installed"
        version = manifest.version if manifest else record.version if record else ""
        lines.append((key, manifest.name if manifest else "", version, state))
    widths = [max(len(line[column]) for line in lines) for column in range(3)]
    for line in lines:
        cells = [
            cell.ljust(width) for cell, width in zip(line[:3], widths, strict=True)
        ]
        print("  ".join([*cells, line[3]]))
    return 0


def _products_install(args: argparse.Namespace) -> int:
    with _engine(load_settings(), owner=True) as engine:
        added = installer.install(engine, args.key)
    if not added:
        print(f"The product {args.key} is already installed.")
    else:
        print(f"Installed {_listed(added)}. {TAKES_EFFECT}")
    return 0


def _products_uninstall(args: argparse.Namespace) -> int:
    with _engine(load_settings(), owner=True) as engine:
        installer.uninstall(engine, args.key, delete_data=args.delete_data)
    if args.delete_data:
        print(f"Removed {args.key} and deleted its data. {TAKES_EFFECT}")
    else:
        print(
            f"Removed {args.key}. Its data is kept, so installing it again "
            f"restores it. {TAKES_EFFECT}"
        )
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn  # Only serving needs the server.

    from bowerstone_engine.main import create_app

    # create_app runs the serving check, so nothing listens on a database it may
    # not use.
    app = create_app(load_settings())
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        server_header=False,
        log_config=_log_config(uvicorn.config.LOGGING_CONFIG),
    )
    return 0


def _check_imports(args: argparse.Namespace) -> int:
    problems = check_imports(args.package)
    for problem in problems:
        print(problem)
    if problems:
        count = len(problems)
        verb = "import breaks" if count == 1 else "imports break"
        print(f"{count} {verb} the rules.", file=sys.stderr)
        return 1
    print(f"No import in {args.package} breaks the rules.")
    return 0


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(
            1, f"{self.prog}: {message}. Run '{self.prog} --help' to see its options.\n"
        )


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog=PROG, description="Run and manage the Bowerstone engine.")
    commands = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    setup = commands.add_parser(
        "setup-database",
        help="Create the database and its two roles, once, on a new install.",
        description=(
            "Create Bowerstone's database and its two roles, with the passwords "
            "and database name in BOWERSTONE_OWNER_DATABASE_URL and "
            "BOWERSTONE_DATABASE_URL. Anything that already exists is left as it is."
        ),
    )
    setup.add_argument(
        "--admin-url",
        help=(
            "An admin's address, such as "
            "postgresql://admin:password@127.0.0.1:5432/postgres. "
            "Other programs on the computer can see a command's arguments, so prefer "
            "setting BOWERSTONE_ADMIN_DATABASE_URL instead."
        ),
    )
    setup.set_defaults(run=_setup_database)

    commands.add_parser(
        "migrate",
        help="Bring the database up to date, as the owner role.",
        description="Check the database, then apply the core's and each enabled "
        "product's new migrations. Prints each migration applied.",
    ).set_defaults(run=_migrate)

    products = commands.add_parser(
        "products", help="List, install, and remove products."
    )
    product_commands = products.add_subparsers(
        dest="product_command", required=True, metavar="<command>"
    )
    product_commands.add_parser(
        "list", help="Every product present: installed, removed, or available."
    ).set_defaults(run=_products_list)
    install = product_commands.add_parser(
        "install", help=f"Install a product and the products it needs. {TAKES_EFFECT}"
    )
    install.add_argument("key", help="The product's key, such as accounting.")
    install.set_defaults(run=_products_install)
    uninstall = product_commands.add_parser(
        "uninstall", help=f"Remove a product, keeping its data. {TAKES_EFFECT}"
    )
    uninstall.add_argument("key", help="The product's key, such as accounting.")
    uninstall.add_argument(
        "--delete-data",
        action="store_true",
        help="Delete the product's data too. "
        "Refused for products whose data must be kept.",
    )
    uninstall.set_defaults(run=_products_uninstall)

    serve = commands.add_parser(
        "serve",
        help="Run the API as the app role.",
        description="Check the database as the app role, "
        "then serve the API and the screens.",
    )
    serve.add_argument(
        "--host",
        default="127.0.0.1",
        help="The address to listen on. Default: 127.0.0.1.",
    )
    serve.add_argument(
        "--port", type=int, default=8000, help="The port to listen on. Default: 8000."
    )
    serve.set_defaults(run=_serve)

    check = commands.add_parser(
        "check-imports",
        help="Check that a product imports the engine and other products only "
        "through their public modules.",
    )
    check.add_argument(
        "package", help="The product's package, such as bowerstone_accounting."
    )
    check.set_defaults(run=_check_imports)
    return parser


@contextmanager
def _engine(settings: Settings, *, owner: bool) -> Iterator[Engine]:
    engine = db.owner_engine(settings) if owner else db.app_engine(settings)
    try:
        yield engine
    finally:
        engine.dispose()


def _log_config(base: dict[str, Any]) -> dict[str, Any]:
    """uvicorn's own logging, plus the engine's log, which records unexpected errors."""
    config = copy.deepcopy(base)
    config["loggers"]["bowerstone_engine"] = {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }
    return config


def _database_problem(error: BaseException | None) -> str:
    lines = str(error).strip().splitlines() if error is not None else []
    detail = lines[0] if lines else "no details"
    if isinstance(error, psycopg.errors.QueryCanceled):
        return f"The database stopped a statement that ran too long: {detail}"
    if isinstance(error, psycopg.OperationalError):
        return f"Cannot reach the database: {detail}"
    return f"The database refused the command: {detail}"


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def _listed(keys: Sequence[str]) -> str:
    if len(keys) == 1:
        return keys[0]
    return f"{', '.join(keys[:-1])} and {keys[-1]}"
