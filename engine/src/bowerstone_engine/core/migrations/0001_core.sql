-- Migration 0001 of the core module: the data every Bowerstone app shares.
-- Platform Build Plan, task 1.3. Runs as bowerstone_owner, never as the app.
-- The migration runner has already created the core schema and
-- core.applied_migration, so this file does not create them.
--
-- Error codes the API translates: BW012 for a change to the change history,
-- BW013 for a time zone that does not exist, BW014 for an invalid event.

-- Besides the owner, only the app role may use the core schema.
REVOKE ALL ON SCHEMA core FROM PUBLIC;
GRANT USAGE ON SCHEMA core TO bowerstone_app;


-- One company per installation (v1 Charter, decision 8), so one row only.
CREATE TABLE core.company (
    id        smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    name      text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200 AND btrim(name) <> ''),
    currency  text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    time_zone text NOT NULL
);

-- A time zone must be one PostgreSQL knows, such as America/Vancouver.
CREATE FUNCTION core.company_check_time_zone() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_timezone_names WHERE name = NEW.time_zone) THEN
        RAISE EXCEPTION 'time zone % does not exist', NEW.time_zone USING ERRCODE = 'BW013';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER company_time_zone_check BEFORE INSERT OR UPDATE OF time_zone ON core.company
    FOR EACH ROW EXECUTE FUNCTION core.company_check_time_zone();


-- Roles and permissions are data, so each app adds its own permissions when
-- it is installed. A permission's key starts with the key of the app that added it.
CREATE TABLE core.role (
    key  text PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{1,30}$'),
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100)
);

CREATE TABLE core.permission (
    key         text PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{1,30}\.[a-z][a-z0-9_]{0,60}$'),
    app_key     text NOT NULL CHECK (app_key ~ '^[a-z][a-z0-9_]{1,30}$'),
    description text NOT NULL CHECK (char_length(description) BETWEEN 1 AND 200),
    CHECK (starts_with(key, app_key || '.'))
);

CREATE TABLE core.role_permission (
    role_key       text NOT NULL REFERENCES core.role (key) ON DELETE CASCADE,
    permission_key text NOT NULL REFERENCES core.permission (key) ON DELETE CASCADE,
    PRIMARY KEY (role_key, permission_key)
);
CREATE INDEX role_permission_permission_idx ON core.role_permission (permission_key);


-- People who can sign in. Empty until sign-in is built.
CREATE TABLE core.app_user (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email         text NOT NULL CHECK (char_length(email) BETWEEN 3 AND 254 AND email LIKE '%_@_%'),
    name          text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200 AND btrim(name) <> ''),
    password_hash text NOT NULL CHECK (char_length(password_hash) BETWEEN 1 AND 500),
    role_key      text NOT NULL REFERENCES core.role (key),
    is_active     boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now()
);
-- One account per email address, whatever its capitals.
CREATE UNIQUE INDEX app_user_email_idx ON core.app_user (lower(email));
CREATE INDEX app_user_role_idx ON core.app_user (role_key);


-- The shared list of customers and suppliers. A party is deactivated rather
-- than deleted, because invoices and other records will point at it.
CREATE TABLE core.party (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200 AND btrim(name) <> ''),
    email       text CHECK (char_length(email) BETWEEN 3 AND 254 AND email LIKE '%_@_%'),
    phone       text CHECK (char_length(phone) BETWEEN 1 AND 50),
    is_customer boolean NOT NULL DEFAULT false,
    is_supplier boolean NOT NULL DEFAULT false,
    is_active   boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now()
);


-- Each installed app. Task 1.4's installer writes this table as the owner.
CREATE TABLE core.installed_app (
    key          text PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{1,30}$'),
    version      text NOT NULL CHECK (char_length(version) BETWEEN 1 AND 50),
    requires     text[] NOT NULL DEFAULT '{}',
    is_enabled   boolean NOT NULL DEFAULT true,
    installed_at timestamptz NOT NULL DEFAULT now()
);


-- The change history, written only by core.write_audit.
CREATE TABLE core.audit_log (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     bigint,
    action      text NOT NULL CHECK (action IN ('insert', 'update', 'delete')),
    record_type text NOT NULL,
    record_id   text NOT NULL,
    before      jsonb,
    after       jsonb,
    at          timestamptz NOT NULL DEFAULT now()
);
-- Finds the history of one record.
CREATE INDEX audit_log_record_idx ON core.audit_log (record_type, record_id, id);

-- The change history is append-only, even for the owner role, and cannot be emptied.
CREATE FUNCTION core.audit_log_refuse_change() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    RAISE EXCEPTION 'the change history cannot be changed' USING ERRCODE = 'BW012';
END $$;

CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON core.audit_log
    FOR EACH ROW EXECUTE FUNCTION core.audit_log_refuse_change();
CREATE TRIGGER audit_log_never_emptied BEFORE TRUNCATE ON core.audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION core.audit_log_refuse_change();

-- Every app attaches this to its tables, so no code path can skip the history.
-- It runs with the owner's rights, because the app role cannot write audit_log.
-- The user comes from bowerstone_engine.db.set_current_user, and is empty when
-- no user is signed in. Password hashes are never recorded.
CREATE FUNCTION core.write_audit() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    v_before jsonb;
    v_after  jsonb;
    v_id     text;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        v_before := to_jsonb(OLD) - 'password_hash';
    END IF;
    IF TG_OP <> 'DELETE' THEN
        v_after := to_jsonb(NEW) - 'password_hash';
    END IF;
    v_id := coalesce(v_after, v_before) ->> 'id';
    IF v_id IS NULL THEN
        RAISE EXCEPTION 'core.write_audit needs an id column on %.%', TG_TABLE_SCHEMA, TG_TABLE_NAME;
    END IF;
    INSERT INTO core.audit_log (user_id, action, record_type, record_id, before, after)
    VALUES (nullif(current_setting('bowerstone.user_id', true), '')::bigint,
            lower(TG_OP), TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME, v_id, v_before, v_after);
    RETURN NULL;
END $$;


-- Events are written in the same transaction as the change, so apps can
-- react later without the first app knowing they exist.
CREATE TABLE core.event (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type        text NOT NULL,
    source      text NOT NULL,
    record_type text NOT NULL,
    record_id   text NOT NULL,
    payload     jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(payload) = 'object'),
    created_at  timestamptz NOT NULL DEFAULT now(),
    handled_at  timestamptz
);
CREATE INDEX event_unhandled_idx ON core.event (id) WHERE handled_at IS NULL;

-- How an app announces a change. It runs with the owner's rights, because the
-- app role cannot insert into core.event directly. Returns the event's id.
CREATE FUNCTION core.emit_event(
    p_type text, p_source text, p_record_type text, p_record_id text, p_payload jsonb DEFAULT '{}'
) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    v_id bigint;
BEGIN
    IF p_source IS NULL OR p_source !~ '^[a-z][a-z0-9_]{1,30}$' THEN
        RAISE EXCEPTION 'event source % is not a valid app key', p_source USING ERRCODE = 'BW014';
    END IF;
    IF p_type IS NULL OR p_type !~ '^[a-z][a-z0-9_]{1,30}\.[a-z][a-z0-9_]{0,60}$'
       OR NOT starts_with(p_type, p_source || '.') THEN
        RAISE EXCEPTION 'event type % must be the source, a dot, and a name, such as %.entry_posted',
            p_type, p_source USING ERRCODE = 'BW014';
    END IF;
    IF p_record_type IS NULL OR char_length(p_record_type) NOT BETWEEN 1 AND 100
       OR p_record_id IS NULL OR char_length(p_record_id) NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'an event needs a record type and a record id of 1 to 100 characters'
            USING ERRCODE = 'BW014';
    END IF;
    IF p_payload IS NULL OR jsonb_typeof(p_payload) <> 'object' THEN
        RAISE EXCEPTION 'an event payload must be a JSON object' USING ERRCODE = 'BW014';
    END IF;
    IF octet_length(p_payload::text) > 65536 THEN
        RAISE EXCEPTION 'an event payload must be at most 64 KB' USING ERRCODE = 'BW014';
    END IF;
    INSERT INTO core.event (type, source, record_type, record_id, payload)
    VALUES (p_type, p_source, p_record_type, p_record_id, p_payload)
    RETURNING id INTO v_id;
    RETURN v_id;
END $$;


-- Starting data.
INSERT INTO core.company (name, currency, time_zone)
VALUES ('My Company', 'CAD', 'America/Vancouver');

INSERT INTO core.role (key, name) VALUES
    ('owner', 'Owner'), ('staff', 'Staff'), ('read_only', 'Read-only');

INSERT INTO core.permission (key, app_key, description) VALUES
    ('core.view', 'core', 'View the company, the customer and supplier list, and the change history'),
    ('core.parties', 'core', 'Add and change customers and suppliers'),
    ('core.company', 'core', 'Change the company''s name and time zone'),
    ('core.users', 'core', 'Manage users and their roles');

INSERT INTO core.role_permission (role_key, permission_key) VALUES
    ('owner', 'core.view'), ('owner', 'core.parties'), ('owner', 'core.company'), ('owner', 'core.users'),
    ('staff', 'core.view'), ('staff', 'core.parties'),
    ('read_only', 'core.view');


-- The change history starts after the starting data.
CREATE TRIGGER audit_company AFTER INSERT OR UPDATE OR DELETE ON core.company
    FOR EACH ROW EXECUTE FUNCTION core.write_audit();
CREATE TRIGGER audit_app_user AFTER INSERT OR UPDATE OR DELETE ON core.app_user
    FOR EACH ROW EXECUTE FUNCTION core.write_audit();
CREATE TRIGGER audit_party AFTER INSERT OR UPDATE OR DELETE ON core.party
    FOR EACH ROW EXECUTE FUNCTION core.write_audit();


-- What the app role may do. It owns nothing, so it cannot drop or empty these
-- tables or switch off their triggers. It writes the change history and events
-- only through core.write_audit and core.emit_event.
GRANT SELECT ON core.company, core.role, core.permission, core.role_permission, core.app_user,
    core.party, core.installed_app, core.audit_log, core.event TO bowerstone_app;
GRANT UPDATE (name, time_zone) ON core.company TO bowerstone_app;
GRANT INSERT (name, email, phone, is_customer, is_supplier, is_active),
      UPDATE (name, email, phone, is_customer, is_supplier, is_active) ON core.party TO bowerstone_app;

-- PostgreSQL lets every role call a new function. Only emit_event is meant to be called.
REVOKE ALL ON FUNCTION core.company_check_time_zone(), core.audit_log_refuse_change(),
    core.write_audit() FROM PUBLIC;
REVOKE ALL ON FUNCTION core.emit_event(text, text, text, text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.emit_event(text, text, text, text, jsonb) TO bowerstone_app;
