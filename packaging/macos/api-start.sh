#!/bin/sh
set -eu
cd /Library/Tel-Agent
if grep -q '^ENCRYPTION_KEY=$' .env; then
  KEY="$(python/bin/python3 -c 'from api.security.crypto import generate_key; print(generate_key())')"
  sed -i '' "s/^ENCRYPTION_KEY=$/ENCRYPTION_KEY=${KEY}/" .env
fi
python/bin/python3 -m alembic upgrade head
exec python/bin/python3 -m api
