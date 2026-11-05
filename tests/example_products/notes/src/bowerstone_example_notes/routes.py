"""The notes product's routes, under /api/v1/notes."""

from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import text

from bowerstone_engine.auth import require
from bowerstone_engine.deps import Db, Pages
from bowerstone_engine.errors import InputError, NotFoundError
from bowerstone_engine.types import Page, page_of


class Note(BaseModel):
    id: int
    body: str


class NewNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: Annotated[str, StringConstraints(min_length=1, max_length=10_000)]


def build_router() -> APIRouter:
    router = APIRouter(tags=["notes"])

    @router.get("/notes", dependencies=[require("notes.view")])
    def list_notes(db: Db, pages: Pages) -> Page[Note]:
        if pages.after is not None and not pages.after.isdigit():
            raise InputError({"query.after": "Pass back a page's next_after value."})
        rows = db.execute(
            text(
                "SELECT id, body FROM notes.note "
                "WHERE id > :after ORDER BY id LIMIT :limit"
            ),
            {"after": int(pages.after or 0), "limit": pages.limit + 1},
        )
        notes = [Note(id=row.id, body=row.body) for row in rows]
        return page_of(notes, pages.limit, key=lambda note: str(note.id))

    @router.post("/notes", status_code=201, dependencies=[require("notes.edit")])
    def add_note(db: Db, note: NewNote) -> Note:
        row = db.execute(
            text("INSERT INTO notes.note (body) VALUES (:body) RETURNING id, body"),
            {"body": note.body},
        ).one()
        return Note(id=row.id, body=row.body)

    @router.get("/notes/{note_id}", dependencies=[require("notes.view")])
    def get_note(db: Db, note_id: int) -> Note:
        row = db.execute(
            text("SELECT id, body FROM notes.note WHERE id = :id"), {"id": note_id}
        ).one_or_none()
        if row is None:
            raise NotFoundError(f"There is no note {note_id}.")
        return Note(id=row.id, body=row.body)

    return router
