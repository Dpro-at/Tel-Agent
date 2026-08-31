"""What the generated OpenAPI document cannot work out on its own — §B6.

FastAPI writes the document from the code, which is why it cannot drift from it. Two
things it has no way to know, and both of them are what a reader of `/docs` needs first:

**How to authenticate.** Authorisation here lives in middleware, not in a dependency on
each route (`api/middleware/auth.py`), and a middleware is invisible to the generator.
So the document was silent on the subject: every operation looked as though it needed
nothing. Declaring the schemes here is documentation and nothing else — the middleware
is still the only thing that enforces anything, and it is unchanged.

**What a group of routes is for.** A tag with no description is a heading; a tag with
one is a map. Twenty tag groups were in use and one was described, so `/docs` listed
eighty-six operations under nineteen unexplained words.

Nothing in this module changes a response. `tests/test_openapi.py` asserts that it stays
that way, and that a tag added to a route without a sentence here fails rather than
quietly appearing as a bare word.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

# The three credentials §B9.1 names, and the paths each one opens.
#
# They are separate schemes rather than one, because that is the specification's whole
# point: a leak of one must not open the others. A document that showed a single
# "Authorization" box would describe a product that does not exist.
SECURITY_SCHEMES: dict[str, dict[str, Any]] = {
    "session": {
        "type": "apiKey",
        "in": "cookie",
        "name": "telagent_session",
        "description": (
            "The dashboard's session. Created by `POST /api/auth/login`, stored as an "
            "HttpOnly cookie, and revoked by signing out. It opens the `/api/…` routes "
            "and **nothing else** — presented at a machine path it is refused. There is "
            "no public signup: the first account is made by `POST /api/setup` and every "
            "later one is invited (D-030)."
        ),
    },
    "hooksToken": {
        "type": "http",
        "scheme": "bearer",
        "description": (
            "A machine token of scope `hooks`, for software posting events to this "
            "installation under `/hooks/…`. Minted from `POST /api/tokens`, shown once, "
            "and stored only as a hash. It does not open `/mcp`, and presented there it "
            "is refused with the same 401 an unknown token gets — a leak must not "
            "confirm itself (§B9.1)."
        ),
    },
    "mcpToken": {
        "type": "http",
        "scheme": "bearer",
        "description": (
            "A machine token of scope `mcp`, for an external model calling this "
            "installation's tools at `/mcp`. Same rules as `hooksToken`, and it does "
            "not open `/hooks/…`. §B9 requires it precisely because a model that can "
            "start real calls spends real money."
        ),
    },
}

# Reachable with no credential at all. Kept as a tuple of prefixes rather than derived
# from `PUBLIC_PATHS`, because these are the *documented* public surface: the widget's
# two routes and the things a monitor or a reverse proxy hits before anybody signs in.
_NO_CREDENTIAL = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/public/",
    "/widget/",
    "/embed.js",
)

# The machine paths, and which scheme opens each. `api/security/machine_tokens.py` is
# the enforcement; this is the sentence about it.
_MACHINE = (("/hooks/", "hooksToken"), ("/mcp", "mcpToken"))

# The first run and the sign-in routes: no credential yet, by definition.
_UNAUTHENTICATED_API = (
    "/api/setup",
    "/api/auth/login",
    "/api/auth/forgot",
    "/api/auth/code/verify",
    "/api/auth/key/challenge",
    "/api/auth/key/verify",
    "/api/auth/password/reset",
    "/api/invites/",
)


def _requirement(path: str) -> list[dict[str, list[str]]]:
    """Which scheme opens this path, said the way OpenAPI says it.

    An empty list is meaningful in OpenAPI: it declares the operation open, rather than
    leaving the reader to guess from silence. That distinction is the reason this
    function exists instead of a dictionary.
    """
    if path.startswith(_NO_CREDENTIAL) or path.startswith(_UNAUTHENTICATED_API):
        return []
    for prefix, scheme in _MACHINE:
        if path.startswith(prefix):
            return [{scheme: []}]
    return [{"session": []}]


def describe(app: FastAPI, tags: list[dict[str, Any]]) -> None:
    """Teach `app.openapi()` the two things the generator cannot see.

    Installed as an override rather than written into each route: a `security` argument
    repeated on eighty-six operations is eighty-six chances to write a different one,
    and the rule it expresses is about paths, not about endpoints.
    """

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=tags,
            license_info=app.license_info,
            contact=app.contact,
        )
        schema.setdefault("components", {})["securitySchemes"] = SECURITY_SCHEMES
        for path, operations in schema["paths"].items():
            requirement = _requirement(path)
            for operation in operations.values():
                if isinstance(operation, dict):
                    operation["security"] = requirement

        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
