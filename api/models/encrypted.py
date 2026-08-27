"""The encrypted column type — E2's "encryption applied per call site is encryption
forgotten at one call site", answered.

A model declares a column `EncryptedStr` once and every write through the ORM is
encrypted, every read decrypted. No route, no service, no script has to remember —
which is the entire point: the one call site that would forget is the one that leaks.

The type needs the key at import-mapping time but must not *read* it then — tests and
tooling import the models without a key configured. So the key is fetched lazily from
settings on first use and cached per process.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import Text, TypeDecorator

from api.config import get_settings
from api.security.crypto import decrypt, encrypt, load_key

logger = logging.getLogger("api.db")

_key_cache: bytes | None = None


def _key() -> bytes:
    global _key_cache
    if _key_cache is None:
        _key_cache = load_key(get_settings().encryption_key)
    return _key_cache


def reset_key_cache() -> None:
    """For tests and for `scripts/rotate_key.py`, which changes the key mid-process."""
    global _key_cache
    _key_cache = None


class EncryptedStr(TypeDecorator):
    """A TEXT column whose value never touches the database in the clear.

    `cache_ok = True`: the type carries no per-instance state, so SQLAlchemy may cache
    compiled statements that use it.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        return encrypt(value, _key())

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        # DecryptionFailed propagates. Returning None here would make a tampered
        # credential indistinguishable from an absent one, and the caller would
        # helpfully ask the user to enter it again - destroying the evidence.
        return decrypt(value, _key())
