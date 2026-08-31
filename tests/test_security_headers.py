"""The headers every response carries — G1.

The first three are ordinary. The last two are the ones worth having a test for: a
middleware that stamps `X-Frame-Options: DENY` on everything, or replaces a policy a
route set on purpose, unembeds every customer's chat widget — and that failure shows up
as an empty iframe on somebody else's website, not as anything in a log here.
"""

from __future__ import annotations

from fastapi import FastAPI, Response
from httpx import ASGITransport, AsyncClient

from api.middleware.security_headers import JSON_POLICY, SecurityHeadersMiddleware


def _app(*, hsts_seconds: int = 0) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, hsts_seconds=hsts_seconds)

    @app.get("/data")
    async def data() -> dict:
        return {"ok": True}

    @app.get("/embeddable")
    async def embeddable() -> Response:
        """A route that has already decided who may frame it — the widget's shape."""
        return Response(
            content="<p>hello</p>",
            media_type="text/html",
            headers={
                "Content-Security-Policy": (
                    "frame-ancestors https://wagner-partner.test; default-src 'none'"
                )
            },
        )

    @app.get("/page")
    async def page() -> Response:
        """HTML with no policy of its own — the documentation's shape."""
        return Response(content="<p>hello</p>", media_type="text/html")

    return app


async def _get(path: str, **kwargs):
    transport = ASGITransport(app=_app(**kwargs), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://localhost") as http:
        return await http.get(path)


async def test_a_json_answer_may_do_nothing_at_all() -> None:
    """It is data. A browser talked into rendering it as a document must not be able to
    fetch, frame or execute anything from it."""
    answer = await _get("/data")

    assert answer.headers["x-content-type-options"] == "nosniff"
    assert answer.headers["referrer-policy"] == "no-referrer"
    assert answer.headers["content-security-policy"] == JSON_POLICY
    assert answer.headers["x-frame-options"] == "DENY"


async def test_a_policy_a_route_set_is_left_alone() -> None:
    """The one that keeps the product working.

    `frame-ancestors` on the widget page is the entire mechanism deciding which sites
    may embed a customer's chat (§B14). Replacing it with something stricter would
    unembed every one of them.
    """
    answer = await _get("/embeddable")

    assert answer.headers["content-security-policy"] == (
        "frame-ancestors https://wagner-partner.test; default-src 'none'"
    )


async def test_a_page_that_says_who_may_frame_it_gets_no_x_frame_options() -> None:
    """The trap next to it.

    `X-Frame-Options` is a separate mechanism from `frame-ancestors`, so a blanket
    `DENY` is honoured by browsers that read it and refuses the embed the CSP allowed.
    """
    answer = await _get("/embeddable")

    assert "x-frame-options" not in answer.headers


async def test_html_with_no_policy_is_framed_by_nobody_and_given_no_policy() -> None:
    """The documentation's case: it loads its viewer from elsewhere, so a policy
    guessed here would break it. Refusing to frame it is still right."""
    answer = await _get("/page")

    assert answer.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" not in answer.headers


async def test_hsts_is_not_sent_unless_somebody_asked_for_it() -> None:
    """A machine on a business network reached over plain HTTP that receives this
    header becomes unreachable from every browser that saw it, for as long as the
    header said, and nothing the server does afterwards takes it back."""
    assert "strict-transport-security" not in (await _get("/data")).headers


async def test_hsts_is_sent_when_it_is() -> None:
    answer = await _get("/data", hsts_seconds=31536000)
    assert answer.headers["strict-transport-security"] == (
        "max-age=31536000; includeSubDomains"
    )


async def test_a_policy_that_refuses_framing_still_gets_the_older_header() -> None:
    """The distinction the rule turns on.

    `frame-ancestors 'none'` is a refusal, not a permission, so the old header says the
    same thing to browsers that read only that one. It is `frame-ancestors <origin>` -
    a deliberate decision to be embeddable - that `DENY` must not overrule.
    """
    answer = await _get("/data")
    assert "frame-ancestors 'none'" in answer.headers["content-security-policy"]
    assert answer.headers["x-frame-options"] == "DENY"
