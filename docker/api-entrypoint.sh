#!/bin/sh
# Migrate, then serve. In that order, always: the schema belongs to the image's
# code, and a container that answers requests against last release's tables is a
# 500 with a long fuse. `python -m api` rather than a bare uvicorn command because
# that is the entry point that honours BIND_HOST and warns when it is widened.
set -e

echo "applying database migrations"
# Retried, because the postgres profile starts the database beside this container
# and it may still be coming up. Ten failures in a row is a real fault; say so.
tries=0
until alembic upgrade head; do
    tries=$((tries + 1))
    if [ "$tries" -ge 10 ]; then
        echo "migrations failed after ${tries} attempts - is the database reachable?" >&2
        exit 1
    fi
    echo "database not ready, retrying (${tries}/10)"
    sleep 3
done

exec python -m api
