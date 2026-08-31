"""The headers every response carries — G1.

Four of them are unconditional and one is not, and the difference is the point of this
module.

**Nothing here overwrites a header a route already set.** That is not politeness, it is
what keeps the product working: `api/routes/widget.py` serves the embeddable chat page
with its own `Content-Security-Policy`, whose `frame-ancestors` is the entire mechanism
deciding which sites may embed a customer's widget (§B14). A middleware that replaced
it with a stricter policy would make the web chat unembeddable everywhere, and the
failure would appear as an empty iframe on a customer's site rather than as anything in
a log here.

**`X-Frame-Options` is decided by the same question**, and it is the trap that is easy
to miss: it is a separate mechanism from `frame-ancestors`, so a browser that honours a
blanket `DENY` refuses the embed even when the CSP allows it.

The rule is therefore not "is there a `frame-ancestors`" but "does it **permit**
anything". A policy that names an origin is a decision to be embeddable, and `DENY`
would overrule it. A policy of `frame-ancestors 'none'` is the same refusal said in the
newer language, and the old header belongs beside it for browsers that read only that
one.

**The policy is chosen by content type.** A JSON response can never legitimately load
anything, so `default-src 'none'` is always correct for it. HTML is left alone: the
documentation loads its viewer from elsewhere and the widget loads its own inline
script, and a middleware guessing one policy for both would break whichever it guessed
against.

**HSTS is off unless asked for, and that is deliberate.** Most installations of this
product are a machine on a business network reached over plain HTTP; sending them a
Strict-Transport-Security header would make the dashboard unreachable from every
browser that saw it, for as long as the header said, with no way to undo it from the
server. It is also unusable on the recommended deployment without a decision this code
cannot make: TLS is terminated by a reverse proxy, so the request arrives as plain
HTTP, and inferring otherwise means trusting a forwarded header from whatever sent it.
An operator who has TLS everywhere sets the number and means it.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# What a JSON response is allowed to do, which is nothing. It is data; a browser that
# is somehow persuaded to render it as a document must not be able to fetch, frame or
# execute anything from it.
JSON_POLICY = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"


def _permits_framing(policy: str) -> bool:
    """Does this policy let anybody frame the response?

    `frame-ancestors 'none'` is a refusal, so it does not count - the old header says
    the same thing to older browsers and belongs beside it. Anything else named there
    is a deliberate decision to be embeddable, and `X-Frame-Options: DENY` would
    overrule it in exactly the browsers that still read it.
    """
    for directive in policy.split(";"):
        name, _, value = directive.strip().partition(" ")
        if name.lower() == "frame-ancestors":
            return value.strip().lower() not in ("", "'none'")
    return False


class SecurityHeadersMiddleware:
    """Add the headers a response does not already carry."""

    def __init__(self, app: ASGIApp, *, hsts_seconds: int = 0) -> None:
        self.app = app
        self.hsts_seconds = hsts_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)

                # No sniffing, ever. A JSON body a browser decides to treat as HTML is
                # the shape half of the old reflected-XSS bugs took.
                headers.setdefault("x-content-type-options", "nosniff")
                # Nothing this API answers is worth telling the next site about, and
                # a path here can carry a conversation handle.
                headers.setdefault("referrer-policy", "no-referrer")

                content_type = headers.get("content-type", "")
                if "content-security-policy" not in headers and content_type.startswith(
                    "application/json"
                ):
                    headers["content-security-policy"] = JSON_POLICY

                if not _permits_framing(headers.get("content-security-policy", "")):
                    headers.setdefault("x-frame-options", "DENY")

                if self.hsts_seconds > 0:
                    headers.setdefault(
                        "strict-transport-security",
                        f"max-age={self.hsts_seconds}; includeSubDomains",
                    )

            await send(message)

        await self.app(scope, receive, with_headers)
