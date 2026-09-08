"""RS256 against a published key set — the shared helper the door channels verify with.

A platform that signs its callbacks with a JWT publishes its public keys at a JWKS
address. The three claims this checks are the three that make a token *ours*: who
issued it, who it was for, and whether it is still alive. Each of them gets its own
test, because a verifier that skips one is a verifier that accepts anybody's token.
"""

from __future__ import annotations

import base64
import datetime as dt
import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    load_pem_private_key,
)

from api.channels.generic import ChannelRefused
from api.channels.jwt import reset_jwks_cache, sign_rs256, verify_rs256

JWKS_URL = "https://keys.example.invalid/jwks"
ISSUER = "chat@example.invalid"
AUDIENCE = "1234567890"
KID = "key-1"


def _jwk(private_key_pem: str, kid: str) -> dict[str, str]:
    """The public half of a key, the way an issuer publishes it."""
    numbers = load_pem_private_key(private_key_pem.encode(), password=None).public_key()
    public = numbers.public_numbers()

    def number(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return {
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "kid": kid,
        "n": number(public.n),
        "e": number(public.e),
    }


@pytest.fixture
def private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()


class FakeIssuer:
    """The platform's key server, and nothing else on that host."""

    def __init__(self, private_key_pem: str) -> None:
        self.private_key_pem = private_key_pem
        self.served = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) != JWKS_URL:
            return httpx.Response(404, json={"error": "unknown"})
        self.served += 1
        return httpx.Response(200, json={"keys": [_jwk(self.private_key_pem, KID)]})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def _claims(**overrides: object) -> dict[str, object]:
    now = dt.datetime.now(tz=dt.UTC)
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": int((now + dt.timedelta(minutes=5)).timestamp()),
        "iat": int(now.timestamp()),
        "sub": "a-space",
    }
    claims.update(overrides)
    return claims


@pytest.fixture(autouse=True)
def _empty_cache():
    reset_jwks_cache()
    yield
    reset_jwks_cache()


async def test_a_token_signed_by_the_published_key_is_accepted(private_key_pem: str) -> None:
    issuer = FakeIssuer(private_key_pem)
    token = sign_rs256(_claims(), private_key_pem, kid=KID)
    async with issuer.client() as client:
        claims = await verify_rs256(
            token, jwks_url=JWKS_URL, issuer=ISSUER, audience=AUDIENCE, client=client
        )
    assert claims["sub"] == "a-space"


async def test_the_key_set_is_fetched_once_and_then_remembered(private_key_pem: str) -> None:
    """A callback per message must not become a key fetch per message."""
    issuer = FakeIssuer(private_key_pem)
    token = sign_rs256(_claims(), private_key_pem, kid=KID)
    async with issuer.client() as client:
        for _ in range(3):
            await verify_rs256(
                token, jwks_url=JWKS_URL, issuer=ISSUER, audience=AUDIENCE, client=client
            )
    assert issuer.served == 1


async def test_a_token_for_somebody_else_is_refused(private_key_pem: str) -> None:
    issuer = FakeIssuer(private_key_pem)
    token = sign_rs256(_claims(aud="another-application"), private_key_pem, kid=KID)
    async with issuer.client() as client:
        with pytest.raises(ChannelRefused):
            await verify_rs256(
                token, jwks_url=JWKS_URL, issuer=ISSUER, audience=AUDIENCE, client=client
            )


async def test_a_token_from_somebody_else_is_refused(private_key_pem: str) -> None:
    issuer = FakeIssuer(private_key_pem)
    token = sign_rs256(_claims(iss="someone-else"), private_key_pem, kid=KID)
    async with issuer.client() as client:
        with pytest.raises(ChannelRefused):
            await verify_rs256(
                token, jwks_url=JWKS_URL, issuer=ISSUER, audience=AUDIENCE, client=client
            )


async def test_an_expired_token_is_refused(private_key_pem: str) -> None:
    issuer = FakeIssuer(private_key_pem)
    past = int((dt.datetime.now(tz=dt.UTC) - dt.timedelta(minutes=5)).timestamp())
    token = sign_rs256(_claims(exp=past), private_key_pem, kid=KID)
    async with issuer.client() as client:
        with pytest.raises(ChannelRefused):
            await verify_rs256(
                token, jwks_url=JWKS_URL, issuer=ISSUER, audience=AUDIENCE, client=client
            )


async def test_a_token_signed_by_a_key_nobody_published_is_refused(
    private_key_pem: str,
) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    issuer = FakeIssuer(private_key_pem)
    token = sign_rs256(_claims(), other_pem, kid=KID)
    async with issuer.client() as client:
        with pytest.raises(ChannelRefused):
            await verify_rs256(
                token, jwks_url=JWKS_URL, issuer=ISSUER, audience=AUDIENCE, client=client
            )


async def test_a_body_that_is_not_a_token_is_refused(private_key_pem: str) -> None:
    issuer = FakeIssuer(private_key_pem)
    async with issuer.client() as client:
        for rubbish in ("", "not.a.token", json.dumps({"iss": ISSUER})):
            with pytest.raises(ChannelRefused):
                await verify_rs256(
                    rubbish,
                    jwks_url=JWKS_URL,
                    issuer=ISSUER,
                    audience=AUDIENCE,
                    client=client,
                )
