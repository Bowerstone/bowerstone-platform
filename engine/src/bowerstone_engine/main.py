"""Building the API: `create_app(settings)`."""

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI
from sqlalchemy import Engine
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

from bowerstone_engine.auth import public, unprotected_routes
from bowerstone_engine.config import Settings, SettingsError
from bowerstone_engine.core.routes import build_api_router, build_core_router
from bowerstone_engine.db import app_engine
from bowerstone_engine.http import EdgeMiddleware, install_error_handlers
from bowerstone_engine.installer import dependency_order, installed_products
from bowerstone_engine.migrate import (
    MigrationRef,
    check_before_serving,
    core_migrations,
    migrations_from_package,
)
from bowerstone_engine.products import (
    ProductError,
    ProductManifest,
    engine_version,
    load_products,
)

API_PREFIX = "/api/v1"
PLACEHOLDER_DIR = Path(__file__).parent / "placeholder"
MIN_DESKTOP_TOKEN_LENGTH = 32


def create_app(
    settings: Settings, products: Mapping[str, ProductManifest] | None = None
) -> FastAPI:
    """Builds the API.

    It refuses production until sign-in exists, and desktop mode without its
    token. It runs the serving form of the database check, then mounts the
    core's routes and each enabled product's routes, under /api/v1. It refuses
    to start if any route uses neither require() nor public(). Everything else
    is the shell, from `static_dir`. `products` defaults to the products found
    through their entry points.
    """
    desktop_token = _check_settings(settings)
    static_dir = settings.static_dir or PLACEHOLDER_DIR
    if not static_dir.is_dir():
        raise SettingsError(f"BOWERSTONE_STATIC_DIR is not a folder: {static_dir}")

    engine = app_engine(settings)
    try:
        manifests = _enabled_products(engine, products)
        app = _build(settings, engine, manifests, static_dir, desktop_token)
    except BaseException:
        engine.dispose()
        raise
    return app


def _check_settings(settings: Settings) -> str | None:
    if settings.env == "production":
        raise SettingsError(
            "BOWERSTONE_ENV is production, but the engine has no sign-in yet, "
            "so it runs only with BOWERSTONE_ENV=development."
        )
    if settings.mode != "desktop":
        return None
    token = settings.desktop_token.get_secret_value() if settings.desktop_token else ""
    if len(token) < MIN_DESKTOP_TOKEN_LENGTH:
        raise SettingsError(
            "BOWERSTONE_DESKTOP_TOKEN must be set, to at least "
            f"{MIN_DESKTOP_TOKEN_LENGTH} characters, in desktop mode."
        )
    return token


def _enabled_products(
    engine: Engine, products: Mapping[str, ProductManifest] | None
) -> list[ProductManifest]:
    """The enabled products, each after the products it needs, once the database
    check has passed for them all."""
    with engine.connect() as conn:
        installed = installed_products(conn)
        enabled = sorted(
            key for key, product in installed.items() if product.is_enabled
        )
        catalog = (load_products() if products is None else products) if enabled else {}
        missing = [key for key in enabled if key not in catalog]
        if missing:
            raise ProductError(
                " ".join(
                    f"The product {key} is installed, but its package is missing."
                    for key in missing
                )
            )
        order = dependency_order(enabled, catalog)
        not_enabled = [key for key in order if key not in enabled]
        if not_enabled:
            raise ProductError(
                f"An enabled product needs {', '.join(not_enabled)}, which is not "
                "installed. Run bowerstone migrate after installing it."
            )
        manifests = [catalog[key] for key in order]
        version = engine_version()
        problems = [problem for m in manifests for problem in m.problems(version)]
        if problems:
            raise ProductError(" ".join(problems))
        expected: list[MigrationRef] = list(core_migrations().refs)
        for manifest in manifests:
            expected.extend(
                migrations_from_package(manifest.key, manifest.migrations_package).refs
            )
        check_before_serving(conn, expected)
    return manifests


def _build(
    settings: Settings,
    engine: Engine,
    manifests: Sequence[ProductManifest],
    static_dir: Path,
    desktop_token: str | None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    # The docs pages load scripts from other sites, which the security headers
    # forbid, so they are off. The schema is served below instead.
    app = FastAPI(
        title="Bowerstone",
        version=engine_version(),
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.products = list(manifests)
    install_error_handlers(app)

    app.include_router(build_api_router(manifests), prefix=API_PREFIX)
    app.include_router(build_core_router(), prefix=f"{API_PREFIX}/core")
    for manifest in manifests:
        if manifest.router is None:
            continue
        router = manifest.router()
        if not isinstance(router, APIRouter):
            raise ProductError(
                f"The product {manifest.key}'s router function "
                "did not return an APIRouter."
            )
        app.include_router(router, prefix=f"{API_PREFIX}/{manifest.key}")

    @app.get(
        f"{API_PREFIX}/openapi.json", dependencies=[public()], include_in_schema=False
    )
    def openapi() -> dict[str, Any]:
        return app.openapi()

    unprotected = unprotected_routes(app)
    if unprotected:
        raise ProductError(
            "Every route must use require() or public(), and these use neither: "
            f"{', '.join(unprotected)}."
        )

    # Any address no route matches.
    app.router.default = ShellFiles(directory=static_dir)
    app.add_middleware(
        EdgeMiddleware,
        max_body_bytes=settings.max_body_bytes,
        desktop_token=desktop_token,
    )
    return app


class ShellFiles(StaticFiles):
    """Serves the built shell for every address no route matches.

    A page address, one whose last part has no dot, falls back to the shell's
    index.html, so the shell can show it. A missing file, and any unknown
    /api/ address, gets the JSON 404 instead.
    """

    def __init__(self, directory: Path) -> None:
        # html=True serves index.html for the folder itself, the address "/".
        super().__init__(directory=directory, html=True)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope["path"])
        if path == "/api" or path.startswith("/api/"):
            raise StarletteHTTPException(status_code=404)
        await super().__call__(scope, receive, send)

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as error:
            if error.status_code != 404 or "." in path.rsplit("/", 1)[-1]:
                raise
            return await super().get_response("index.html", scope)
