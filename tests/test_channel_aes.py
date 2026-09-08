"""AES-CBC with PKCS#7 — the shared helper two callback formats need.

Nothing here invents a primitive; `cryptography` does the cipher. What this module
owns is the padding, and the failure worth a test is the quiet one: a block that
does not authenticate must raise rather than return plausible-looking bytes.
"""

from __future__ import annotations

import pytest

from api.channels.aes import decrypt_cbc, encrypt_cbc

KEY = bytes(range(32))
IV = bytes(range(16))


def test_a_message_survives_the_round_trip() -> None:
    plain = b"an appointment on Tuesday"
    assert decrypt_cbc(KEY, IV, encrypt_cbc(KEY, IV, plain)) == plain


def test_an_empty_message_is_a_whole_block_of_padding() -> None:
    """PKCS#7 always pads, so a zero-length message is one full block."""
    sealed = encrypt_cbc(KEY, IV, b"")
    assert len(sealed) == 32
    assert decrypt_cbc(KEY, IV, sealed) == b""


def test_the_block_size_is_the_callers_to_choose() -> None:
    sealed = encrypt_cbc(KEY, IV, b"x", block=16)
    assert len(sealed) == 16
    assert decrypt_cbc(KEY, IV, sealed, block=16) == b"x"


def test_a_payload_that_is_not_whole_blocks_is_refused() -> None:
    with pytest.raises(ValueError):
        decrypt_cbc(KEY, IV, b"short")


def test_bad_padding_is_refused_rather_than_trimmed() -> None:
    """A tampered final block must not be quietly truncated into a shorter message."""
    sealed = bytearray(encrypt_cbc(KEY, IV, b"an appointment"))
    sealed[-1] ^= 0xFF
    with pytest.raises(ValueError):
        decrypt_cbc(KEY, IV, bytes(sealed))
