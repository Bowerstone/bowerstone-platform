-- Undoes migration 0001 of the core module. Platform Build Plan, task 1.3.
-- Leaves the core schema and core.applied_migration, which the runner owns.
-- Fails if an app still uses core.write_audit or core.emit_event, as it should.

DROP FUNCTION core.emit_event(text, text, text, text, jsonb);
DROP TABLE core.event;
DROP TABLE core.audit_log;
DROP FUNCTION core.audit_log_refuse_change();
DROP TABLE core.party;
DROP TABLE core.app_user;
DROP TABLE core.installed_app;
DROP TABLE core.role_permission;
DROP TABLE core.permission;
DROP TABLE core.role;
DROP TABLE core.company;
DROP FUNCTION core.company_check_time_zone();
DROP FUNCTION core.write_audit();
REVOKE USAGE ON SCHEMA core FROM bowerstone_app;
