-- The tasks product: tasks that may point at a note. It needs the notes
-- product, but keeps no foreign key into its schema, so each product's
-- schema stays its own.
CREATE SCHEMA tasks;

CREATE TABLE tasks.task (
    id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title   text NOT NULL,
    note_id bigint,
    done    boolean NOT NULL DEFAULT false
);

CREATE TRIGGER audit AFTER INSERT OR UPDATE OR DELETE ON tasks.task
    FOR EACH ROW EXECUTE FUNCTION core.write_audit();

GRANT USAGE ON SCHEMA tasks TO bowerstone_app;
GRANT SELECT, INSERT, UPDATE ON tasks.task TO bowerstone_app;
