-- The core schema: the data every product shares.
--
-- Runs as bowerstone_owner. The migration runner has already created the core
-- schema and core.applied_migration.

-- The one company this installation keeps books for.
CREATE TABLE core.company (
    id        smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    name      text NOT NULL CHECK (btrim(name) <> ''),
    currency  text NOT NULL DEFAULT 'CAD' CHECK (currency ~ '^[A-Z]{3}$'),
    -- AT TIME ZONE raises an error for a time zone Postgres does not know.
    time_zone text NOT NULL DEFAULT 'UTC'
        CHECK ((timestamptz '2000-01-01 00:00:00+00' AT TIME ZONE time_zone) IS NOT NULL)
);

-- Roles and permissions are data. Each product adds its own permissions when
-- it is installed, and removes them when its data is deleted.
CREATE TABLE core.role (
    key  text PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]*$'),
    name text NOT NULL
);

CREATE TABLE core.permission (
    key         text PRIMARY KEY,
    description text NOT NULL,
    -- 'core', or the key of the product that added the permission.
    module      text NOT NULL,
    CHECK (key ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'),
    CHECK (starts_with(key, module || '.'))
);

CREATE TABLE core.role_permission (
    role_key       text NOT NULL REFERENCES core.role (key) ON DELETE CASCADE,
    permission_key text NOT NULL REFERENCES core.permission (key) ON DELETE CASCADE,
    PRIMARY KEY (role_key, permission_key)
);
CREATE INDEX role_permission_permission_idx ON core.role_permission (permission_key);

-- People who can sign in. Empty until sign-in exists.
CREATE TABLE core.app_user (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email         text NOT NULL,
    name          text NOT NULL,
    password_hash text NOT NULL,
    role_key      text NOT NULL REFERENCES core.role (key),
    is_active     boolean NOT NULL DEFAULT true
);
-- One person per email address, whatever its capitals.
CREATE UNIQUE INDEX app_user_email_key ON core.app_user (lower(email));

-- Each installed product. A removed product stays here, disabled, with its data.
CREATE TABLE core.installed_product (
    key          text PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{1,30}$'),
    version      text NOT NULL,
    requires     text[] NOT NULL DEFAULT '{}',
    is_enabled   boolean NOT NULL DEFAULT true,
    installed_at timestamptz NOT NULL DEFAULT now()
);

-- The change history. Triggers write it, and no one may change or delete a row.
CREATE TABLE core.audit_log (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     bigint,
    action      text NOT NULL CHECK (action IN ('insert', 'update', 'delete')),
    -- The table, as schema.table.
    record_type text NOT NULL,
    record_id   text NOT NULL,
    before      jsonb,
    after       jsonb,
    at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_record_idx ON core.audit_log (record_type, record_id);

-- Events, written in the same transaction as the change they announce.
CREATE TABLE core.event (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type        text NOT NULL,
    source      text NOT NULL,
    record_type text NOT NULL,
    record_id   text NOT NULL,
    payload     jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(payload) = 'object'),
    created_at  timestamptz NOT NULL DEFAULT now(),
    handled_at  timestamptz,
    -- A type starts with its source, as in accounting.entry_posted.
    CHECK (starts_with(type, source || '.'))
);
CREATE INDEX event_unhandled_idx ON core.event (id) WHERE handled_at IS NULL;

-- The audit trigger function. Every product attaches it to its own tables:
--
--   CREATE TRIGGER audit AFTER INSERT OR UPDATE OR DELETE ON accounting.account
--       FOR EACH ROW EXECUTE FUNCTION core.write_audit();
--
-- The record's ID comes from its id column, or from the columns named as the
-- trigger's arguments, joined with '/'. The user comes from the setting
-- bowerstone.user_id, which the engine sets for each transaction.
-- Password hashes are left out.
CREATE FUNCTION core.write_audit() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    v_before jsonb;
    v_after  jsonb;
    v_row    jsonb;
    v_parts  text[] := '{}';
BEGIN
    IF TG_OP <> 'INSERT' THEN
        v_before := to_jsonb(OLD) - 'password_hash';
    END IF;
    IF TG_OP <> 'DELETE' THEN
        v_after := to_jsonb(NEW) - 'password_hash';
    END IF;
    v_row := coalesce(v_after, v_before);

    IF TG_NARGS = 0 THEN
        v_parts := ARRAY[v_row ->> 'id'];
    ELSE
        FOR i IN 0 .. TG_NARGS - 1 LOOP
            v_parts := v_parts || (v_row ->> TG_ARGV[i]);
        END LOOP;
    END IF;
    IF array_position(v_parts, NULL) IS NOT NULL THEN
        RAISE EXCEPTION 'core.write_audit: %.% has no value in its key column',
            TG_TABLE_SCHEMA, TG_TABLE_NAME;
    END IF;

    INSERT INTO core.audit_log (user_id, action, record_type, record_id, before, after)
    VALUES (nullif(current_setting('bowerstone.user_id', true), '')::bigint,
            lower(TG_OP),
            TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME,
            array_to_string(v_parts, '/'),
            v_before,
            v_after);
    RETURN NULL;
END $$;

-- How a product announces a change. The app role cannot write core.event
-- directly, so this runs as its owner. Returns the event's id.
CREATE FUNCTION core.emit_event(
    p_type        text,
    p_source      text,
    p_record_type text,
    p_record_id   text,
    p_payload     jsonb DEFAULT '{}'
) RETURNS bigint
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    INSERT INTO core.event (type, source, record_type, record_id, payload)
    VALUES (p_type, p_source, p_record_type, p_record_id, coalesce(p_payload, '{}'))
    RETURNING id
$$;

-- The audit log is append-only, even for the owner role. TRUNCATE does not
-- fire row triggers, so it has its own.
CREATE FUNCTION core.refuse_audit_change() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    RAISE EXCEPTION 'the change history cannot be changed or deleted' USING ERRCODE = 'BW012';
END $$;

CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON core.audit_log
    FOR EACH ROW EXECUTE FUNCTION core.refuse_audit_change();
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON core.audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION core.refuse_audit_change();

-- Starting data. It goes in before the audit triggers, so the history starts empty.
INSERT INTO core.company (name) VALUES ('My Company');

INSERT INTO core.role (key, name) VALUES
    ('owner',     'Owner'),
    ('staff',     'Staff'),
    ('read_only', 'Read-only');

INSERT INTO core.permission (key, description, module) VALUES
    ('core.view',    'View the company details and the change history', 'core'),
    ('core.company', 'Change the company details',                      'core'),
    ('core.users',   'Manage users and their roles',                    'core');

INSERT INTO core.role_permission (role_key, permission_key) VALUES
    ('owner',     'core.view'),
    ('owner',     'core.company'),
    ('owner',     'core.users'),
    ('staff',     'core.view'),
    ('read_only', 'core.view');

-- The change history of the core's own tables.
CREATE TRIGGER audit AFTER INSERT OR UPDATE OR DELETE ON core.company
    FOR EACH ROW EXECUTE FUNCTION core.write_audit();
CREATE TRIGGER audit AFTER INSERT OR UPDATE OR DELETE ON core.app_user
    FOR EACH ROW EXECUTE FUNCTION core.write_audit();
CREATE TRIGGER audit AFTER INSERT OR UPDATE OR DELETE ON core.role
    FOR EACH ROW EXECUTE FUNCTION core.write_audit('key');
CREATE TRIGGER audit AFTER INSERT OR UPDATE OR DELETE ON core.permission
    FOR EACH ROW EXECUTE FUNCTION core.write_audit('key');
CREATE TRIGGER audit AFTER INSERT OR UPDATE OR DELETE ON core.role_permission
    FOR EACH ROW EXECUTE FUNCTION core.write_audit('role_key', 'permission_key');
CREATE TRIGGER audit AFTER INSERT OR UPDATE OR DELETE ON core.installed_product
    FOR EACH ROW EXECUTE FUNCTION core.write_audit('key');

-- What the app role may do. It owns nothing, so it cannot drop or empty a
-- table, or switch off a trigger. It reads every core table, may change the
-- company's details, and may announce events. Sign-in adds its grants on
-- app_user later.
REVOKE ALL ON FUNCTION core.write_audit() FROM PUBLIC;
REVOKE ALL ON FUNCTION core.refuse_audit_change() FROM PUBLIC;
REVOKE ALL ON FUNCTION core.emit_event(text, text, text, text, jsonb) FROM PUBLIC;

GRANT USAGE ON SCHEMA core TO bowerstone_app;
GRANT SELECT ON core.company, core.role, core.permission, core.role_permission,
                core.app_user, core.installed_product, core.audit_log, core.event
    TO bowerstone_app;
GRANT UPDATE (name, currency, time_zone) ON core.company TO bowerstone_app;
GRANT EXECUTE ON FUNCTION core.emit_event(text, text, text, text, jsonb) TO bowerstone_app;
