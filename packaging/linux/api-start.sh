#!/bin/sh
# The API service's start command: migrate, then serve - the same order and the
# same entry point as the container (`python -m api` honours BIND_HOST and warns
# when it is widened). A schema that can lag its code turns every upgrade into a
# 500 hunt.
set -eu
cd /opt/tel-agent
/opt/tel-agent/venv/bin/python -m alembic upgrade head
exec /opt/tel-agent/venv/bin/python -m api
