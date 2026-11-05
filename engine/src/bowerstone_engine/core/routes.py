"""The core's routes: health, the installed products, and the company's details."""

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from bowerstone_engine.auth import public, require
from bowerstone_engine.deps import Db
from bowerstone_engine.errors import InputError
from bowerstone_engine.http import error_body
from bowerstone_engine.products import ProductManifest


class Health(BaseModel):
    status: str


class InstalledProduct(BaseModel):
    key: str
    name: str
    version: str
    requires: list[str]
    # The screen kit versions the product's screens work with.
    ui_versions: str
    # Where the product's built screens are served, or None if it has none.
    screens: str | None


class Company(BaseModel):
    name: str
    currency: str
    time_zone: str


class CompanyChanges(BaseModel):
    """The details to change. A detail left out, or null, stays as it is."""

    model_config = ConfigDict(extra="forbid")

    name: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
        ]
        | None
    ) = None
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None = None
    time_zone: (
        Annotated[str, StringConstraints(min_length=1, max_length=100)] | None
    ) = None


def build_api_router(products: Sequence[ProductManifest]) -> APIRouter:
    """Routes at the top of /api/v1: health and the installed products."""
    router = APIRouter(tags=["engine"])

    @router.get("/health", dependencies=[public()], response_model=Health)
    def health(request: Request) -> Health | JSONResponse:
        # Ready means the database answers, not only the engine.
        engine: Engine = request.app.state.engine
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return JSONResponse(
                error_body("database", "The engine cannot reach its database."), 503
            )
        return Health(status="ok")

    @router.get("/products", dependencies=[require("core.view")])
    def installed_products() -> list[InstalledProduct]:
        return [
            InstalledProduct(
                key=product.key,
                name=product.name,
                version=product.version,
                requires=list(product.requires),
                ui_versions=product.ui_versions,
                screens=f"/products/{product.key}/screens/"
                if product.screens_package
                else None,
            )
            for product in products
        ]

    return router


def build_core_router() -> APIRouter:
    """Routes under /api/v1/core."""
    router = APIRouter(tags=["core"])

    @router.get("/company", dependencies=[require("core.view")])
    def get_company(db: Db) -> Company:
        return _company(db)

    @router.patch("/company", dependencies=[require("core.company")])
    def change_company(db: Db, changes: CompanyChanges) -> Company:
        if (
            changes.time_zone is not None
            and not db.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_timezone_names WHERE name = :name)"
                ),
                {"name": changes.time_zone},
            ).scalar_one()
        ):
            raise InputError(
                {
                    "body.time_zone": "This is not a time zone name, "
                    "such as America/Toronto."
                }
            )
        values = changes.model_dump()
        # A request that changes nothing writes nothing, so the history stays clean.
        if any(value is not None for value in values.values()):
            db.execute(
                text(
                    "UPDATE core.company SET name = coalesce(:name, name), "
                    "currency = coalesce(:currency, currency), "
                    "time_zone = coalesce(:time_zone, time_zone)"
                ),
                values,
            )
        return _company(db)

    return router


def _company(db: Db) -> Company:
    row = db.execute(text("SELECT name, currency, time_zone FROM core.company")).one()
    return Company(name=row.name, currency=row.currency, time_zone=row.time_zone)
