"""What other products may use from notes."""

from sqlalchemy import Connection, text


def note_exists(conn: Connection, note_id: int) -> bool:
    """Whether the note `note_id` exists."""
    return bool(
        conn.execute(
            text("SELECT EXISTS (SELECT 1 FROM notes.note WHERE id = :id)"),
            {"id": note_id},
        ).scalar_one()
    )
