#!/bin/sh
# Entry point of the web container.
#
# node_modules is a volume, so it outlives the container. This installs the
# packages again whenever package-lock.json has changed since the last install,
# then runs the command it was given.
set -eu

stamp=node_modules/.installed-package-lock.json

if [ -f package-lock.json ] && ! cmp -s package-lock.json "$stamp"; then
    npm ci
    cp package-lock.json "$stamp"
fi

exec "$@"
