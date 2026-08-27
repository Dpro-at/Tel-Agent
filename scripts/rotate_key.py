"""Re-encrypt every stored credential under a new key — E5.

A key that cannot be rotated is a key that will not be, including after it leaks. Run:

    python scripts/rotate_key.py --new-key <64 hex chars>

with `ENCRYPTION_KEY` in the environment still set to the OLD key. When the pass
finishes, put the new key into `.env` and restart. The order matters: the running
application keeps decrypting with the old key until the restart, and every row this
script has touched is already readable by the new one only - which is why the script
must finish before the switch.

**Resumable and safe to run twice.** Each row is tried against the new key first: if it
already decrypts, it was done in a previous pass and is skipped. A crash halfway
therefore loses nothing - run it again. Rows that decrypt with neither key are
reported and left untouched: they were corrupt before rotation started, and destroying
the evidence would turn a diagnosable problem into a mystery.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from api.config import get_settings
from api.db import create_engine, create_sessionmaker, session_scope
from api.security.crypto import DecryptionFailed, decrypt, encrypt, load_key

# Every encrypted column in the schema, listed here so rotation cannot silently skip
# one. A new encrypted column is added to this list in the same commit that adds it -
# the test suite asserts the list matches the models.
ENCRYPTED_COLUMNS: list[tuple[str, str, str]] = [
    ("channels", "id", "credentials_encrypted"),
    ("settings", "id", "secret_value"),
]


async def rotate(new_key_hex: str, batch_size: int = 500) -> int:
    settings = get_settings()
    old_key = load_key(settings.encryption_key)
    new_key = load_key(new_key_hex)

    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    rewritten = skipped = corrupt = 0

    try:
        async with session_scope(sessionmaker) as db:
            for table, id_column, column in ENCRYPTED_COLUMNS:
                # Raw SQL on purpose: the ORM's EncryptedStr would decrypt with the
                # process key on read and re-encrypt on write, which is exactly the
                # magic this script must step around to hold both keys at once.
                from sqlalchemy import text as sql

                rows = await db.execute(
                    sql(f"SELECT {id_column}, {column} FROM {table} WHERE {column} IS NOT NULL")  # noqa: S608 - identifiers from the fixed list above
                )
                for row_id, stored in rows:
                    try:
                        decrypt(stored, new_key)
                        skipped += 1  # already rotated in an earlier pass
                        continue
                    except DecryptionFailed:
                        pass

                    try:
                        plaintext = decrypt(stored, old_key)
                    except DecryptionFailed:
                        corrupt += 1
                        print(
                            f"  !! {table}.{column} id={row_id}: decrypts with "
                            "neither key - left untouched"
                        )
                        continue

                    await db.execute(
                        sql(f"UPDATE {table} SET {column} = :value WHERE {id_column} = :id"),  # noqa: S608
                        {"value": encrypt(plaintext, new_key), "id": row_id},
                    )
                    rewritten += 1
                    if rewritten % batch_size == 0:
                        # Commit in batches: a crash loses at most one batch of work,
                        # and the resume check makes even that free.
                        await db.commit()
            await db.commit()
    finally:
        await engine.dispose()

    print(f"rotated {rewritten}, already done {skipped}, corrupt {corrupt}")
    if corrupt:
        print("Corrupt rows are listed above. Investigate before switching the key.")
        return 1
    print("Now set ENCRYPTION_KEY to the new key in .env and restart the server.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new-key", required=True, help="64 hex characters")
    raise SystemExit(asyncio.run(rotate(parser.parse_args().new_key)))
