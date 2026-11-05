"""What routes ask for: the database connection, the current user, and list pages.

    @router.get("/notes", dependencies=[require("notes.view")])
    def list_notes(db: Db, pages: Pages) -> Page[Note]: ...

Products may import this module. It is part of the engine's SDK.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query, Request
from sqlalchemy import Connection, Engine

from bowerstone_engine.db import set_current_user
from bowerstone_engine.types import MAX_PAGE_SIZE


@dataclass(frozen=True)
class User:
    """The person a request runs for."""

    # None for the built-in local user, which has no row in core.app_user.
    id: int | None
    name: str
    role: str


LOCAL_USER = User(id=None, name="Local user", role="owner")
"""Every request runs as this user until sign-in exists. It has the owner role's
permissions. The engine refuses to start in production until then."""


def current_user(request: Request) -> User:
    """The user the request runs for."""
    return LOCAL_USER


CurrentUser = Annotated[User, Depends(current_user)]


def database(request: Request, user: CurrentUser) -> Iterator[Connection]:
    """One connection and one transaction per request, as the app role.

    The transaction commits when the route succeeds, before the response is
    sent, and rolls back when it fails. It names the user for the change history.
    """
    engine: Engine = request.app.state.engine
    with engine.connect() as conn:
        set_current_user(conn, user.id)
        yield conn
        conn.commit()


# scope="function" ends the transaction before the response is sent, so a
# failed commit can never follow a success response.
Db = Annotated[Connection, Depends(database, scope="function")]


@dataclass(frozen=True)
class PageQuery:
    """Which page of a list to show: the rows after `after`, up to `limit` of them."""

    after: str | None
    limit: int


def page_query(
    after: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
) -> PageQuery:
    return PageQuery(after, limit)


Pages = Annotated[PageQuery, Depends(page_query)]
