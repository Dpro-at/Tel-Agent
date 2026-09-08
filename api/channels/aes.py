"""AES-CBC with PKCS#7, for the platforms whose callbacks arrive encrypted.

Some platforms do not sign a callback, they encrypt its body under a key the customer
generated in their own console. That is a signature scheme by another name and it gets
the same treatment: verified before anything is parsed, and one refusal for every
failure.

Nothing here implements a primitive — `cryptography` does the cipher, and no SDK is
added for it (D-044). What this module owns is the padding, and one decision inside it:
the block size is the caller's, because at least one platform pads to 32 bytes rather
than to AES's own 16. Padding that does not check out raises rather than being trimmed
into a shorter message, for the same reason `api/security/crypto.py` refuses a
ciphertext that fails authentication: a quietly truncated body is a body somebody
touched.
"""

from __future__ import annotations

import hmac

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_AES_BLOCK = 16


class PaddingError(ValueError):
    """The decrypted block did not end in valid PKCS#7 padding."""


def _check(key: bytes, iv: bytes, block: int) -> None:
    if len(key) not in (16, 24, 32):
        raise ValueError(f"an AES key is 16, 24 or 32 bytes; this one is {len(key)}")
    if len(iv) != _AES_BLOCK:
        raise ValueError(f"an AES-CBC IV is {_AES_BLOCK} bytes; this one is {len(iv)}")
    if not 1 <= block <= 255 or block % _AES_BLOCK:
        raise ValueError(f"the padding block must be a multiple of {_AES_BLOCK}, up to 255")


def encrypt_cbc(key: bytes, iv: bytes, plain: bytes, *, block: int = 32) -> bytes:
    """Pad to `block` bytes with PKCS#7, then encrypt."""
    _check(key, iv, block)
    fill = block - (len(plain) % block)
    padded = plain + bytes([fill]) * fill
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def decrypt_cbc(key: bytes, iv: bytes, payload: bytes, *, block: int = 32) -> bytes:
    """Decrypt, then strip the PKCS#7 padding — or refuse."""
    _check(key, iv, block)
    if not payload or len(payload) % _AES_BLOCK:
        raise PaddingError("the payload is not a whole number of AES blocks")

    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(payload) + decryptor.finalize()

    fill = padded[-1]
    if not 1 <= fill <= block or fill > len(padded):
        raise PaddingError("the padding length is not a length this block size can produce")
    # Constant time, because this comparison runs on attacker-supplied ciphertext and
    # a padding oracle is built out of exactly this kind of early exit.
    if not hmac.compare_digest(padded[-fill:], bytes([fill]) * fill):
        raise PaddingError("the padding bytes do not agree with the padding length")
    return padded[:-fill]
