"""RS256 tokens: verifying a platform's callback, and signing our own request.

Some platforms prove a callback with a JWT signed by a key they publish at a JWKS
address rather than with an HMAC over the body. The skill's rule is the same either
way — verified before anything is parsed, and every failure gets one refusal — and the
three claims that make a token *ours* are checked here, not by the caller: who issued
it, who it was for, and whether it is still alive. A verifier that skips one of those
accepts anybody's valid token.

`cryptography` does the signature; no JWT library is added (D-044). The key set is
fetched once per address and kept for a day, because a platform that publishes keys
expects them to be cached and a fetch per callback would put a network round trip on
the door. A day of cache is only safe because a token naming a key the cache does not
hold buys one refetch — otherwise a signing key rotated early would shut the door for
up to a day, with every genuine delivery refused for the whole of it.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import time
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from api.channels.generic import ChannelRefused

# How long a fetched key set is trusted. Platforms rotate signing keys on the order of
# weeks and publish the new one before they use it, so a day is cache, not staleness.
JWKS_SECONDS = 24 * 60 * 60

# The shortest gap between two *unscheduled* fetches of one key set. A token naming a
# key we have never seen is what a rotation looks like from here, so it is worth one
# fetch — but it is also what a stranger's forged header looks like, and a door that
# fetched on every one of those would let anybody aim this installation at the issuer.
# One a minute is fast enough for a rotation and useless as a hammer.
JWKS_REFETCH_SECONDS = 60

# Room for the clock on the platform's side to differ from this machine's.
CLOCK_SKEW_SECONDS = 60

_JWKS_CACHE: dict[str, tuple[float, dict[str, rsa.RSAPublicKey]]] = {}

# When each address was last refetched out of turn, for the rate limit above.
_JWKS_REFETCHED: dict[str, float] = {}


def reset_jwks_cache() -> None:
    """For tests, and for an operator who has just been told a key rotated early."""
    _JWKS_CACHE.clear()
    _JWKS_REFETCHED.clear()


def _b64url_decode(value: str) -> bytes:
    padding_needed = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding_needed)
    except (ValueError, TypeError) as error:
        raise ChannelRefused("the token is not base64url") from error


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _public_key(jwk: dict[str, Any]) -> rsa.RSAPublicKey | None:
    if jwk.get("kty") != "RSA" or "n" not in jwk or "e" not in jwk:
        return None
    modulus = int.from_bytes(_b64url_decode(str(jwk["n"])), "big")
    exponent = int.from_bytes(_b64url_decode(str(jwk["e"])), "big")
    return rsa.RSAPublicNumbers(exponent, modulus).public_key()


async def _key_set(jwks_url: str, client: httpx.AsyncClient) -> dict[str, rsa.RSAPublicKey]:
    """The published keys for one address, from the cache when it is still warm."""
    cached = _JWKS_CACHE.get(jwks_url)
    if cached is not None and cached[0] > time.monotonic():
        return cached[1]
    return await _fetch_key_set(jwks_url, client)


async def _refetched_key_set(
    jwks_url: str, client: httpx.AsyncClient, known: dict[str, rsa.RSAPublicKey]
) -> dict[str, rsa.RSAPublicKey]:
    """One extra fetch, because a token names a key this address has not published yet.

    A signing key that rotates early would otherwise deafen the door until the day's
    cache expired: every real delivery would arrive signed by a key we refuse to look
    up. So an unknown `kid` buys a refetch — at most one per `JWKS_REFETCH_SECONDS`
    per address, so that a stranger sending forged headers cannot turn this door into
    a hammer aimed at the issuer. A refetch that fails leaves the keys we already have,
    and the token is judged against those.
    """
    now = time.monotonic()
    last = _JWKS_REFETCHED.get(jwks_url)
    if last is not None and now - last < JWKS_REFETCH_SECONDS:
        return known
    _JWKS_REFETCHED[jwks_url] = now
    try:
        return await _fetch_key_set(jwks_url, client)
    except ChannelRefused:
        return known


async def _fetch_key_set(
    jwks_url: str, client: httpx.AsyncClient
) -> dict[str, rsa.RSAPublicKey]:
    try:
        response = await client.get(jwks_url)
    except httpx.HTTPError as error:
        raise ChannelRefused(f"the key set could not be fetched: {error}") from error
    if response.status_code >= 400:
        raise ChannelRefused(f"the key set answered {response.status_code}")

    try:
        body = response.json()
    except ValueError as error:
        raise ChannelRefused("the key set is not JSON") from error

    keys: dict[str, rsa.RSAPublicKey] = {}
    for entry in body.get("keys", []) if isinstance(body, dict) else []:
        if not isinstance(entry, dict):
            continue
        key = _public_key(entry)
        if key is not None:
            keys[str(entry.get("kid") or "")] = key
    if not keys:
        raise ChannelRefused("the key set published no usable RSA key")

    _JWKS_CACHE[jwks_url] = (time.monotonic() + JWKS_SECONDS, keys)
    return keys


def _split(token: str) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ChannelRefused("the token does not have three parts")
    try:
        header = json.loads(_b64url_decode(parts[0]))
        claims = json.loads(_b64url_decode(parts[1]))
    except ValueError as error:
        raise ChannelRefused("the token's header or claims are not JSON") from error
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise ChannelRefused("the token's header or claims are not objects")
    signed = f"{parts[0]}.{parts[1]}".encode("ascii")
    return header, claims, signed, _b64url_decode(parts[2])


async def verify_rs256(
    token: str,
    *,
    jwks_url: str,
    issuer: str,
    audience: str,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """Check one token against the issuer's published keys, and return its claims.

    Raises `ChannelRefused` for every failure — a bad signature, an unknown key, the
    wrong issuer, the wrong audience, an expired token — because the door that calls
    this answers all of them with the same 403 and has nothing to gain from the
    distinction.
    """
    header, claims, signed, signature = _split(token)
    if header.get("alg") != "RS256":
        raise ChannelRefused(f"the token is signed with {header.get('alg')!r}, not RS256")

    keys = await _key_set(jwks_url, client)
    kid = str(header.get("kid") or "")
    if kid not in keys:
        # Either the issuer rotated, or this is a stranger. One refetch tells us which,
        # and the rate limit inside is what keeps the second case cheap.
        keys = await _refetched_key_set(jwks_url, client, keys)
    candidates = [keys[kid]] if kid in keys else list(keys.values())

    for key in candidates:
        try:
            key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
            break
        except InvalidSignature:
            continue
    else:
        raise ChannelRefused("no published key signed this token")

    if str(claims.get("iss") or "") != issuer:
        raise ChannelRefused("the token was issued by somebody else")

    stated = claims.get("aud")
    heard = stated if isinstance(stated, list) else [stated]
    if audience not in [str(one) for one in heard]:
        raise ChannelRefused("the token was meant for somebody else")

    expiry = claims.get("exp")
    if not isinstance(expiry, int | float):
        raise ChannelRefused("the token does not say when it expires")
    if float(expiry) + CLOCK_SKEW_SECONDS < dt.datetime.now(tz=dt.UTC).timestamp():
        raise ChannelRefused("the token has expired")

    return claims


def sign_rs256(claims: dict[str, Any], private_key_pem: str, kid: str | None = None) -> str:
    """Sign a token with our own key — what a platform asks for in return.

    The mirror image of the above: a few platforms authenticate *us* by a JWT we sign
    with a key we generated in the customer's own developer account.
    """
    key = load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("RS256 needs an RSA private key")

    header: dict[str, Any] = {"alg": "RS256", "typ": "JWT"}
    if kid:
        header["kid"] = kid

    segments = [
        _b64url_encode(json.dumps(part, separators=(",", ":"), sort_keys=True).encode())
        for part in (header, claims)
    ]
    signed = ".".join(segments).encode("ascii")
    signature = key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
    return ".".join([*segments, _b64url_encode(signature)])
