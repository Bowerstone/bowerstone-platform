"""The tasks product's routes, under /api/v1/tasks."""

from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import text

from bowerstone_engine.auth import require
from bowerstone_engine.deps import Db
from bowerstone_engine.errors import RuleError
from bowerstone_engine.types import Money
from bowerstone_example_notes.public import note_exists


class Task(BaseModel):
    id: int
    title: str
    note_id: int | None
    cost: Money | None
    done: bool


class NewTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    note_id: int | None = None
    cost: Money | None = None


def build_router() -> APIRouter:
    router = APIRouter(tags=["tasks"])

    @router.post("/tasks", status_code=201, dependencies=[require("tasks.edit")])
    def add_task(db: Db, task: NewTask) -> Task:
        if task.note_id is not None and not note_exists(db, task.note_id):
            raise RuleError(
                "tasks.note_exists",
                f"There is no note {task.note_id} for this task to point at.",
            )
        row = db.execute(
            text(
                "INSERT INTO tasks.task (title, note_id, cost) "
                "VALUES (:title, :note_id, :cost) "
                "RETURNING id, title, note_id, cost, done"
            ),
            task.model_dump(),
        ).one()
        return Task.model_validate(row._asdict())

    return router
