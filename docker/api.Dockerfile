# The API and the agent loop, in one container - §B10.
#
# Build context is the repository root:
#   docker build -f docker/api.Dockerfile .
#
# The entrypoint migrates before it serves: an image whose schema can lag its code
# turns every upgrade into a 500 hunt, and the handover has the scar to prove it.

FROM python:3.12-slim AS build

WORKDIR /app

# Dependencies first, on their own layer, so editing a source file does not
# reinstall the world. Setuptools refuses to even report requirements without the
# package directories, so two empty ones stand in for them on this layer.
COPY pyproject.toml README.md ./
RUN mkdir -p api agent && pip install --no-cache-dir .

COPY api/ api/
COPY agent/ agent/
COPY locales/ locales/
COPY alembic/ alembic/
COPY alembic.ini ./
COPY scripts/ scripts/
# The package itself again, now that its source is present.
RUN pip install --no-cache-dir --no-deps --force-reinstall .

# ---------------------------------------------------------------------------

FROM python:3.12-slim

# Never root: this process parses strangers' input for a living (§B14).
RUN useradd --create-home --uid 1000 telagent
WORKDIR /app

COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin /usr/local/bin
COPY --from=build /app /app
COPY docker/api-entrypoint.sh /entrypoint.sh

# Conversations, the SQLite database and backups live here, on a volume.
RUN mkdir -p /data && chown telagent:telagent /data /app
VOLUME /data

USER telagent

# Inside the container the server must listen on the bridge interface - the host
# decides what is published, and the compose file publishes loopback only.
ENV BIND_HOST=0.0.0.0 \
    BIND_PORT=38472 \
    DATABASE_URL=sqlite+aiosqlite:////data/tel-agent.db

EXPOSE 38472

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:38472/health', timeout=4)"]

# Via `sh` rather than directly: a COPY from a Windows checkout has no execute
# bit to preserve, and the script does not need one this way.
ENTRYPOINT ["sh", "/entrypoint.sh"]
