"""Who may embed the web widget — §B14's guard, and the reasoning behind each refusal.

This is the only unauthenticated surface in the product. There is no session to check
and no provider signature to verify, so what stands between a visitor's message and the
database is this file.

It is separate from the route on purpose. The decision "may this page post here" is
worth reading on its own, worth testing on its own, and will be read again by whoever
adds the second public endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class Refusal:
    """Why a request was refused, in a form the route turns into an envelope.

    The `code` is for the client; the `reason` is for the log. They differ on purpose:
    a stranger learns that the request was refused, and the operator reading the log
    learns which of the four checks refused it.
    """

    code: str
    reason: str


# Told to the caller when the origin is not allowed.
#
# One code for "no Origin header", "malformed", "not on the list" and "the channel has
# no list yet". Distinguishing them would let somebody probing an address learn whether
# a channel exists and how it is configured, which is exactly what the address alone
# must not reveal.
_REFUSED = "origin_not_allowed"


def normalise_origin(raw: str) -> str | None:
    """An origin as the browser sends it, or nothing.

    Scheme, host, and port when it is not the default. Anything with a path, a query,
    credentials, or a missing host is not an origin, and a value that has to be
    repaired to match is a value that must not match.
    """
    candidate = raw.strip()
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.hostname:
        return None
    # `urlparse` puts everything after the host into these. An "origin" carrying any of
    # them is not one, and accepting it would mean `https://evil.test/#https://ok.test`
    # gets a chance to be compared as a string somewhere later.
    if parsed.path or parsed.query or parsed.fragment or parsed.username:
        return None

    host = parsed.hostname.lower()
    try:
        port = parsed.port
    except ValueError:
        # A port that is not a number. urlparse only raises on access, which is why
        # this is here and not above.
        return None

    default = 443 if parsed.scheme == "https" else 80
    if port is None or port == default:
        return f"{parsed.scheme}://{host}"
    return f"{parsed.scheme}://{host}:{port}"


def check_origin(
    sent: str | None, allowed: list[str] | None, *, own: str | None = None
) -> Refusal | None:
    """None when the page may post here. A `Refusal` otherwise.

    **`own` is the installation's own origin, and it is accepted.** The widget runs in
    an iframe served by this installation, so the browser stamps *that* origin on the
    message it posts - not the site the iframe is embedded on. Checking only the
    customer's list would refuse the widget on every page including the allowed ones,
    which is a guard that stops nothing and everything.

    Accepting it costs nothing a browser can abuse: a page on `evil.test` cannot send
    `Origin: https://telagent.example`, because the browser writes that header and
    scripts cannot. What may is a client that is not a browser, and the guards for that
    are the rate limit and the captcha - neither of which an origin check was ever going
    to provide.

    **What actually decides who may embed the widget is `frame-ancestors`** on the
    widget page (§B14): the browser refuses to render it inside a page that is not on
    the list, and unlike a header comparison that cannot be forged at all.

    Four ways to be refused, one answer to the caller:

    * **No `Origin` header.** A browser sets it on every cross-origin POST and page
      script cannot forge it. Absent means the request did not come from a page, which
      a widget request always does.
    * **Not a well-formed origin.**
    * **The channel has no list.** Not configured is refused, not open: the safe
      reading of an empty list is the one that stores nothing.
    * **Not on the list.**
    """
    if not allowed:
        return Refusal(_REFUSED, "channel has no allowed origins configured")
    if sent is None:
        return Refusal(_REFUSED, "no Origin header")

    origin = normalise_origin(sent)
    if origin is None:
        return Refusal(_REFUSED, f"malformed Origin {sent!r}")

    # Both sides normalised, so `https://Shop.test:443` and `https://shop.test` are the
    # same origin - and a stored value that cannot be normalised matches nothing rather
    # than matching by accident.
    permitted = {normalise_origin(entry) for entry in allowed}
    permitted.discard(None)
    if own is not None:
        permitted.add(normalise_origin(own))
        permitted.discard(None)
    if origin not in permitted:
        return Refusal(_REFUSED, f"origin {origin} not in allowlist")
    return None
