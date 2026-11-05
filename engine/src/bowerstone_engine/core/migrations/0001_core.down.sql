-- Undoes 0001_core. The core schema and core.applied_migration belong to the
-- migration runner, so they stay.

DROP TABLE core.event, core.audit_log, core.installed_product, core.app_user,
           core.role_permission, core.permission, core.role, core.company;

DROP FUNCTION core.emit_event(text, text, text, text, jsonb);
DROP FUNCTION core.write_audit();
DROP FUNCTION core.refuse_audit_change();
