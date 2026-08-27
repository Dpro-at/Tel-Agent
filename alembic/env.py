"""Alembic environment, wired to the application's settings.

The database URL is **not** read from `alembic.ini`. It comes from the same
`api.config.Settings` the running application uses, so a migration cannot be applied to
a different database than the one the API talks to — which is how a schema and an
application drift apart without anybody noticing.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

# Importing the models registers them on `Base.metadata`. Without this import
# autogenerate compares against an empty metadata and cheerfully writes a migration
# that drops every table.
import api.models  # noqa: F401
from alembic import context
from api.config import get_settings
from api.db import Base, create_engine

config = context.config

if config.config_file_name is not None:
    # `disable_existing_loggers=False` is load-bearing, and the default is True.
    #
    # With the default, configuring Alembic's logging switches off every logger that
    # already exists - which, whenever a migration is run inside the application's own
    # process, means the application stops logging and nothing says why.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def render_item(type_: str, obj: object, autogen_context: object) -> object:
    """Render a custom column type as a bare name, and import it.

    Autogenerate writes a custom type by its full path — `api.models.encrypted.
    EncryptedStr()` — without importing anything, so the generated migration raises
    `NameError` the first time it runs. Handling it here means every future encrypted
    column is rendered correctly instead of hand-patched after each generation.
    """
    from api.models.encrypted import EncryptedStr

    if type_ == "type" and isinstance(obj, EncryptedStr):
        autogen_context.imports.add(  # type: ignore[attr-defined]
            "from api.models.encrypted import EncryptedStr"
        )
        return "EncryptedStr()"
    return False


# Objects autogenerate must not touch.
#
# Both of these were caught by reading a generated migration before running it, and
# both would have been destructive.
#
# 1. **The FTS5 shadow tables.** `messages_fts` and its four internal companions are
#    created by raw DDL in the first migration and are not in `Base.metadata`, so
#    autogenerate sees tables it does not recognise and writes `drop_table` for each -
#    which deletes the search index and the triggers that maintain it.
#
# 2. **CHECK constraints.** A constraint produced by `Enum(create_constraint=True)` is
#    emitted with its column, not as a separate object in the metadata. Autogenerate
#    reflects it from the database, finds no counterpart, and writes `drop_constraint`
#    for every enumerated column in the schema. Comparing them is therefore switched
#    off: enum constraints follow their column, and any other CHECK is written by hand.
_UNMANAGED_TABLE_PREFIXES = ("messages_fts",)


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    if type_ == "table" and name and name.startswith(_UNMANAGED_TABLE_PREFIXES):
        return False
    if type_ == "check_constraint":
        return False
    return True


def _url() -> str:
    return get_settings().database_url


def _configure(connection: Connection) -> None:
    """Options that apply to both offline and online runs."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite cannot ALTER a constraint. Batch mode rebuilds the table instead,
        # which is what makes one migration script work against both dialects (D-029).
        render_as_batch=connection.dialect.name == "sqlite",
        # Without this a column that only changed type is silently ignored by
        # autogenerate, and the migration looks complete while doing nothing.
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
        render_item=render_item,
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it.

    The path a database administrator takes when the application is not allowed to run
    DDL against production itself.
    """
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Connect with the application's own engine and run the migrations."""
    engine: AsyncEngine = create_engine(get_settings())

    async with engine.connect() as connection:
        await connection.run_sync(_run)

    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
