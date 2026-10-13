#!/bin/sh
# Health check for the development database (Platform Build Plan, task 0.2).
#
# Passes only when the server is PostgreSQL 18, the database uses the built-in
# C.UTF-8 locale, and both Bowerstone roles exist. It connects over TCP, which
# the image's first-start setup never listens on, so it cannot pass before the
# roles script has run and the real server has started.
#
# When a check fails it prints the reason and exits 1. `docker inspect` shows it.
set -eu

result=$(PGPASSWORD="$POSTGRES_PASSWORD" psql --host=127.0.0.1 --username="$POSTGRES_USER" \
    --dbname="$POSTGRES_DB" --no-psqlrc --tuples-only --no-align --quiet --command="
SELECT CASE
    WHEN current_setting('server_version_num')::int / 10000 <> 18
        THEN 'the server is not PostgreSQL 18'
    WHEN datlocprovider <> 'b' OR datlocale IS DISTINCT FROM 'C.UTF-8'
        THEN 'the database does not use the built-in C.UTF-8 locale'
    WHEN (SELECT count(*) FROM pg_roles
           WHERE rolname IN ('bowerstone_owner', 'bowerstone_app')) <> 2
        THEN 'a Bowerstone role is missing'
    ELSE 'ok'
END
FROM pg_database WHERE datname = current_database()")

if [ "$result" != "ok" ]; then
    echo "$result" >&2
    exit 1
fi
