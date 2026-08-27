"""`/health` answers, and the documentation renders.

These are the two acceptance conditions B1 states, asserted rather than described.
"""

from __future__ import annotations

from httpx import AsyncClient

from api.config import Settings


async def test_health_answers_200(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["environment"] == "development"
    assert body["version"]


async def test_health_reports_the_installed_version(
    client: AsyncClient, settings: Settings
) -> None:
    """The version is read from package metadata, so it cannot drift from pyproject."""
    response = await client.get("/health")

    assert response.json()["version"] == settings.version


async def test_openapi_schema_renders(client: AsyncClient) -> None:
    """`/docs` is generated from this schema; if the schema builds, the page renders."""
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "Tel-Agent"
    assert schema["info"]["license"]["name"] == "AGPL-3.0-or-later"
    assert "/health" in schema["paths"]


async def test_an_unknown_route_is_401_to_a_stranger_not_404(client: AsyncClient) -> None:
    """Closed by default (D5) answers before routing does, and that is the better answer.

    A 404 tells an unauthenticated caller that the path does not exist, which means a
    401 tells them one does. Answering 401 for both makes the route table unreadable
    from outside. A signed-in user still gets a 404 for a genuine typo - asserted in
    `tests/test_auth.py`.
    """
    response = await client.get("/does-not-exist")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
