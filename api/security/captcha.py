"""reCAPTCHA v3 — §B14's third layer, and the first outbound call this product makes.

The origin allowlist stops other sites. The rate limit stops a flood. Neither stops a
bot running on a page the business allowed, which is the case that costs money: an
unauthenticated endpoint that reaches a paid model is worth automating against.

**Optional, and that is not a hedge.** A self-hosted product cannot make a third party
mandatory - reCAPTCHA is Google's, and it sees the visitor. An installation that will
not accept that switches it off and keeps the other two layers, which is a decision the
operator makes rather than a gap we left.

**"We could not ask" and "we asked and it said no" are different answers**, and this is
the whole of the design here. A network failure means Google was unreachable - common
on a machine behind a firewall that was never opened - and refusing every visitor
because of it would take a business's chat offline for a reason that has nothing to do
with the visitor. That case allows and logs loudly. A verified low score is Google
answering, and that refuses.

The secret never reaches a log, an error message, or a response. It is read from an
encrypted column and used once.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger("api.captcha")

VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"

# A visitor is waiting on this. Two seconds is long enough for a healthy round trip and
# short enough that an unreachable Google costs the visitor a pause rather than the
# request. There is no retry: a second attempt doubles the wait and, on an endpoint
# somebody is flooding, doubles the outbound traffic too.
TIMEOUT = httpx.Timeout(2.0)

# Google's default. Below it is "probably automated", not "certainly", and the failure
# mode of a higher one is refusing real customers - which is why it is configurable per
# channel and why this is where it starts.
DEFAULT_THRESHOLD = 0.5

# The action name the widget sends with its token. Checked because a token is issued
# for one action on one page: without this, a token minted on a public sign-up form of
# the same site would pass here.
ACTION = "web_chat_message"


@dataclass(frozen=True)
class Verdict:
    """What came back, in the two shapes the caller acts on."""

    ok: bool
    # Why it was refused, for the log. Never returned to the caller - a bot learning
    # which check it failed is a bot that stops failing it.
    reason: str = ""
    score: float | None = None


ALLOWED = Verdict(ok=True)


async def verify(
    secret: str | None, token: str | None, *, threshold: float | None = None
) -> Verdict:
    """Ask Google about one token.

    `secret` is None when the channel has no reCAPTCHA configured, and then this does
    nothing at all - not even a request. That is the switched-off case, and it must be
    free rather than merely permissive.
    """
    if not secret:
        return ALLOWED

    if not token:
        # Configured but nothing sent. Either the widget failed to load Google's script
        # or the caller is not the widget; both are refused, because the point of
        # turning this on is that a message without a token does not pass.
        return Verdict(ok=False, reason="no captcha token")

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(VERIFY_URL, data={"secret": secret, "response": token})
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError) as thrown:
        # Could not ask. Allowed, and loud: an installation whose firewall blocks
        # Google is one where this layer is silently absent, and the operator should
        # find that out from their own log rather than from a bill.
        #
        # `thrown` is logged by type and message; it cannot carry the secret, which is
        # in the request body and never in an httpx exception's text.
        logger.warning(
            "captcha could not be verified, allowing",
            extra={"error": type(thrown).__name__, "detail": str(thrown)[:200]},
        )
        return ALLOWED

    if not body.get("success"):
        # Google answered. `error-codes` says why - an expired token, a duplicate, a
        # secret that does not match the site key.
        codes = body.get("error-codes") or []
        return Verdict(ok=False, reason=f"rejected: {','.join(str(code) for code in codes)}")

    action = body.get("action")
    if action != ACTION:
        return Verdict(ok=False, reason=f"token was minted for {action!r}, not {ACTION!r}")

    score = body.get("score")
    if not isinstance(score, int | float):
        # A success without a score is v2's shape, which means the key pair is v2 and
        # the threshold below would compare against nothing.
        return Verdict(ok=False, reason="no score in the answer - is this a v3 key?")

    limit = DEFAULT_THRESHOLD if threshold is None else threshold
    if score < limit:
        return Verdict(ok=False, reason=f"score {score} below {limit}", score=float(score))

    return Verdict(ok=True, score=float(score))
