#!/bin/sh
# Creates the two Bowerstone roles in the development database.
#
# The Postgres image runs this once, when its data folder is empty. Changing a
# password later needs ALTER ROLE, or `make clean` to start from nothing.
#
# The image sources this file instead of running it when it is not executable,
# so the script leaves the shell options alone and checks its inputs itself.

: "${BOWERSTONE_OWNER_PASSWORD:?BOWERSTONE_OWNER_PASSWORD must be set}"
: "${BOWERSTONE_APP_PASSWORD:?BOWERSTONE_APP_PASSWORD must be set}"

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
