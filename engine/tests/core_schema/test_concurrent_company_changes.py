"""Two requests changing the company row at the same time (CLAUDE.md, concurrency tests)."""

import threading
from collections.abc import Callable

from pydantic import SecretStr
from sqlalchemy import Engine, text

from bowerstone_engine.db import set_current_user


def test_two_concurrent_renames_are_both_applied_and_both_recorded(
    core_database: str,
    app_url_for: Callable[[str], SecretStr],
    pool_factory: Callable[..., Engine],
) -> None:
    """The second waits for the first, then applies on top of it; neither is lost."""
    pool = pool_factory(app_url_for(core_database), pool_size=2)
    both_ready = threading.Barrier(2, timeout=10)
    errors: list[BaseException] = []

    def rename(user_id: int, name: str) -> None:
        """Rename the company as the given user, starting together with the other thread."""
        try:
            with pool.begin() as connection:
                set_current_user(connection, user_id)
                both_ready.wait()
                connection.execute(
                    text("UPDATE core.company SET name = :name"), {"name": name}
                )
        except BaseException as error:
            errors.append(error)  # the main thread fails the test with it

    threads = [
        threading.Thread(target=rename, args=(1, "First Name Ltd.")),
        threading.Thread(target=rename, args=(2, "Second Name Ltd.")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []

    with pool.connect() as connection:
        history = connection.execute(
            text(
                "SELECT user_id, before ->> 'name' AS old, after ->> 'name' AS new "
                "FROM core.audit_log WHERE record_type = 'core.company' ORDER BY id"
            )
        ).all()
        final_name = connection.execute(
            text("SELECT name FROM core.company")
        ).scalar_one()
    assert sorted(row.user_id for row in history) == [1, 2]
    first, second = history
    assert first.old == "My Company"
    assert second.old == first.new
    assert final_name == second.new
