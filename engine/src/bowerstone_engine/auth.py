"""Permissions on every route.

Every route declares require("<permission>") or public() in its dependencies:

    @router.get("/accounts", dependencies=[require("accounting.view")])
    @router.get("/health", dependencies=[public()])

The engine refuses to start if any route declares neither.

Products may import this module. It is part of the engine's SDK.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import FastAPI, params
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy import Connection, text

from bowerstone_engine.deps import CurrentUser, Db, User
from bowerstone_engine.errors import PermissionDenied

PUBLIC = "public"


class PermissionCheck:
    """The dependency `require` adds. It refuses a user without the permission."""

    def __init__(self, permission: str) -> None:
        self.permission = permission

    def __call__(self, user: CurrentUser, db: Db) -> None:
        if not has_permission(db, user, self.permission):
            raise PermissionDenied(
                f"You need the permission {self.permission} to do this."
            )


def require(permission: str) -> params.Depends:
    """Lets a route run only for users whose role has `permission`."""
    return params.Depends(PermissionCheck(permission))


def _public() -> None:
    """Marks a route anyone may use."""


def public() -> params.Depends:
    """Lets anyone use a route."""
    return params.Depends(_public)


def has_permission(conn: Connection, user: User, permission: str) -> bool:
    """Whether the user's role has `permission`."""
    return bool(
        conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM core.role_permission "
                "WHERE role_key = :role AND permission_key = :permission)"
            ),
            {"role": user.role, "permission": permission},
        ).scalar_one()
    )


@dataclass(frozen=True)
class RouteAccess:
    """One route as the app serves it, and what it requires."""

    methods: tuple[str, ...]
    path: str
    # The permissions it requires, or {"public"}. Empty when it declares neither.
    access: frozenset[str]
    is_api_route: bool


def route_access(app: FastAPI) -> list[RouteAccess]:
    """Every route the app serves, with its full path and what it requires.

    Routes come from FastAPI's route contexts, so routes added through
    include_router count with their prefix and their router's dependencies.
    """
    routes = []
    for context in iter_route_contexts(app.routes):
        dependant = getattr(context, "dependant", None)
        routes.append(
            RouteAccess(
                methods=tuple(sorted(context.methods or ())),
                path=context.path or "",
                access=frozenset(_markers(dependant)) if dependant else frozenset(),
                is_api_route=isinstance(context.original_route, APIRoute),
            )
        )
    return routes


def unprotected_routes(app: FastAPI) -> list[str]:
    """Every route that uses neither require() nor public(), or mixes public()
    with require(), as "METHOD /path"."""
    problems: list[str] = []
    for route in route_access(app):
        mixed = PUBLIC in route.access and len(route.access) > 1
        if not route.is_api_route or not route.access or mixed:
            problems.extend(
                f"{method} {route.path}" for method in route.methods or ("?",)
            )
    return problems


def _markers(dependant: Dependant) -> Iterator[str]:
    for dependency in dependant.dependencies:
        if isinstance(dependency.call, PermissionCheck):
            yield dependency.call.permission
        elif dependency.call is _public:
            yield PUBLIC
        yield from _markers(dependency)
