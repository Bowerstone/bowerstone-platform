-- The notes product: one table of notes.
CREATE SCHEMA notes;

CREATE TABLE notes.note (
    id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    body text NOT NULL
);

CREATE TRIGGER audit AFTER INSERT OR UPDATE OR DELETE ON notes.note
    FOR EACH ROW EXECUTE FUNCTION core.write_audit();

GRANT USAGE ON SCHEMA notes TO bowerstone_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON notes.note TO bowerstone_app;
