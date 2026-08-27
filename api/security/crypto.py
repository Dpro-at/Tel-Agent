"""Encryption at rest for user-entered credentials — E1 and E2.

§B9.2 splits secrets into two homes: installation secrets in `.env`, user-entered
credentials in the database, encrypted. `ENCRYPTION_KEY` in `.env` is the bridge — it
must never sit in the same place as the data it protects, which is also why it must be
**backed up separately from the database**: a backup containing both is a backup that
undoes the whole arrangement.

Two failure modes, both silent until the day they are not:

* **Lose the key** and every stored credential is unrecoverable. There is no reset.
* **Leak it** and the encryption bought nothing.

So the key is validated at startup and the process refuses to run without a usable one
rather than falling back to plaintext — a fallback would be the quietest possible way
to store credentials unencrypted for months.

The cipher is AES-256-GCM from the `cryptography` package. Nothing here implements a
primitive; this module only decides the envelope: a version byte, a fresh 12-byte nonce
per encryption, then the ciphertext with its authentication tag. The version byte is
what lets the scheme change later without a flag day — a future decryptor dispatches on
it, and `scripts/rotate_key.py` can migrate rows one version to the next.
"""

from __future__ import annotations

import base64
import binascii
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# The envelope version this code writes. Bump when the scheme changes; keep the old
# branch in `decrypt` until rotation has rewritten every row.
VERSION = 1

_KEY_BYTES = 32  # AES-256
_NONCE_BYTES = 12  # the GCM standard size


class EncryptionKeyError(RuntimeError):
    """The configured key cannot be used. The message says what to do."""


class DecryptionFailed(ValueError):
    """The stored value did not authenticate.

    Raised on a tampered or truncated ciphertext, and on one encrypted under a
    different key. Never returns garbage: GCM authenticates before it decrypts, and a
    credential that fails authentication is a credential somebody touched.
    """


def load_key(encryption_key: str | None) -> bytes:
    """Validate the configured key and return its raw bytes.

    Accepts exactly what `.env.example` documents: 64 hex characters, as
    `openssl rand -hex 32` produces. Anything else refuses with instructions rather
    than being padded, truncated or hashed into shape — silently 'fixing' a key means
    two installations can believe they hold the same key while holding different ones.
    """
    if not encryption_key:
        raise EncryptionKeyError(
            "ENCRYPTION_KEY is not set. Credentials cannot be stored without it. "
            "Generate one with:  openssl rand -hex 32  - then keep a copy somewhere "
            "that is NOT the database backup: a backup containing both the key and "
            "the data it protects defeats the encryption."
        )
    try:
        raw = binascii.unhexlify(encryption_key.strip())
    except (binascii.Error, ValueError) as error:
        raise EncryptionKeyError(
            "ENCRYPTION_KEY is not valid hex. Expected 64 hex characters "
            "(openssl rand -hex 32)."
        ) from error
    if len(raw) != _KEY_BYTES:
        raise EncryptionKeyError(
            f"ENCRYPTION_KEY is {len(raw)} bytes; AES-256 needs exactly {_KEY_BYTES}. "
            "Generate one with:  openssl rand -hex 32"
        )
    return raw


def generate_key() -> str:
    """A fresh key in the configured format, for the installer."""
    return secrets.token_hex(_KEY_BYTES)


def encrypt(plaintext: str, key: bytes) -> str:
    """Encrypt one value. Returns text safe for a TEXT column.

    base64 rather than raw bytes so the column stays readable-as-a-column in psql and
    in backups - unreadable in *content*, but visibly an encrypted blob rather than
    mojibake that invites someone to "fix the encoding".
    """
    nonce = secrets.token_bytes(_NONCE_BYTES)
    sealed = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    envelope = bytes([VERSION]) + nonce + sealed
    return base64.b64encode(envelope).decode("ascii")


def decrypt(stored: str, key: bytes) -> str:
    """Open one stored value, or raise `DecryptionFailed`."""
    try:
        envelope = base64.b64decode(stored.encode("ascii"), validate=True)
    except (binascii.Error, ValueError) as error:
        raise DecryptionFailed("stored value is not a valid envelope") from error

    if len(envelope) < 1 + _NONCE_BYTES + 16:  # version + nonce + GCM tag
        raise DecryptionFailed("stored value is too short to be an envelope")

    version = envelope[0]
    if version != VERSION:
        raise DecryptionFailed(f"unknown envelope version {version}")

    nonce = envelope[1 : 1 + _NONCE_BYTES]
    sealed = envelope[1 + _NONCE_BYTES :]
    try:
        plaintext = AESGCM(key).decrypt(nonce, sealed, None)
    except InvalidTag as error:
        raise DecryptionFailed(
            "stored value failed authentication - tampered, truncated, or encrypted "
            "under a different key"
        ) from error
    return plaintext.decode("utf-8")


def mask(value: str) -> str:
    """The only form of a credential a client is ever shown — E3.

    Last four characters, per §B9. Short values mask entirely: revealing four of six
    characters is most of the secret.
    """
    if len(value) <= 8:
        return "••••"
    return f"••••{value[-4:]}"
