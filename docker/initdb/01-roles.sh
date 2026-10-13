#!/bin/sh
# docker/initdb/01-roles.sh: runs once, when the database folder is empty
#
# Creates the two roles Bowerstone uses, as in the Initial Setup Plan, section
# "Database roles". bowerstone_owner owns the database and runs migrations.
# bowerstone_app is the running engine: it may connect, and owns nothing.
# Changing a password later needs an ALTER ROLE command.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v owner_pw="$BOWERSTONE_OWNER_PASSWORD" \
     -v app_pw="$BOWERSTONE_APP_PASSWORD" \
     -v db="$POSTGRES_DB" <<'SQL'
CREATE ROLE bowerstone_owner LOGIN PASSWORD :'owner_pw';
CREATE ROLE bowerstone_app   LOGIN PASSWORD :'app_pw';
ALTER DATABASE :"db" OWNER TO bowerstone_owner;
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO bowerstone_app;
SQL
