"""What the running API's role, bowerstone_app, may and may not do in the core schema.

PostgreSQL refuses every statement below with error 42501 (insufficient
privilege), so a bug in the engine or an app cannot do any of it.
"""

from collections.abc import Callable

import pytest
from sqlalchemy import Connection, text

Refusal = Callable[..., str]

READABLE_TABLES = [
    "company",
    "role",
    "permission",
    "role_permission",
    "app_user",
    "party",
    "installed_app",
    "audit_log",
    "event",
]

REFUSED = [
    # The change history: only core.write_audit writes it.
    "INSERT INTO core.audit_log (action, record_type, record_id) VALUES ('insert', 'core.x', '1')",
    "UPDATE core.audit_log SET record_id = '2'",
    "DELETE FROM core.audit_log",
    "TRUNCATE core.audit_log",
    # Events: only core.emit_event writes them.
    "INSERT INTO core.event (type, source, record_type, record_id) VALUES ('core.x', 'core', 'x', '1')",
    "UPDATE core.event SET handled_at = now()",
    # Installed apps, roles, and permissions belong to the installer.
    "INSERT INTO core.installed_app (key, version) VALUES ('demo', '1.0.0')",
    "UPDATE core.installed_app SET is_enabled = false",
    "DELETE FROM core.installed_app",
    "INSERT INTO core.role (key, name) VALUES ('boss', 'Boss')",
    "DELETE FROM core.role",
    "INSERT INTO core.permission (key, app_key, description) VALUES ('core.x', 'core', 'x')",
    "INSERT INTO core.role_permission VALUES ('staff', 'core.users')",
    "DELETE FROM core.role_permission",
    # Users wait for the sign-in plan.
    "INSERT INTO core.app_user (email, name, password_hash, role_key) VALUES ('a@b.io', 'A', 'h', 'owner')",
    "UPDATE core.app_user SET role_key = 'owner'",
    # The company: one row, whose currency the app cannot change.
    "UPDATE core.company SET currency = 'USD'",
    "INSERT INTO core.company (name, currency, time_zone) VALUES ('X', 'CAD', 'UTC')",
    "DELETE FROM core.company",
    "TRUNCATE core.company",
    # Parties are deactivated, never deleted, and their timestamps are the database's.
    "DELETE FROM core.party",
    "TRUNCATE core.party",
    "UPDATE core.party SET created_at = now()",
    # The schema itself: the app role owns nothing.
    "ALTER TABLE core.party DISABLE TRIGGER audit_party",
    "ALTER TABLE core.audit_log DISABLE TRIGGER ALL",
    "DROP TABLE core.party",
    "CREATE TABLE core.sneaky (id integer)",
    "SELECT * FROM core.applied_migration",
]


@pytest.mark.parametrize("table", READABLE_TABLES)
def test_the_app_role_may_read_every_shared_table(
    as_app: Connection, table: str
) -> None:
    """Reading is allowed everywhere the API needs it. The API checks permissions."""
    as_app.execute(text(f"SELECT * FROM core.{table} LIMIT 1"))  # noqa: S608 - fixed table names


@pytest.mark.parametrize("statement", REFUSED)
def test_the_app_role_is_refused(
    as_app: Connection, refusal: Refusal, statement: str
) -> None:
    """Each statement fails with insufficient privilege."""
    assert refusal(as_app, statement) == "42501"
