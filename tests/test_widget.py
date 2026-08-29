"""The widget's two public GETs.

The one that matters is `frame-ancestors`: it is what actually decides who may embed
the chat, because the `Origin` on a message is stamped with the iframe's own origin and
never with the site the iframe sits in. A test that only checked the message endpoint
would have let this ship believing the allowlist protected the embed, which it does not
on its own.
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import Channel, Workspace

ALLOWED = "https://shop.test"


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    workspace = Workspace(name="Wagner & Partner")
    migrated.add(workspace)
    await migrated.flush()

    channels = {
        "live": Channel(
            workspace_id=workspace.id,
            kind="web",
            name="Web chat",
            webhook_path="live-" + "a" * 28,
            settings_json={"allowed_origins": [ALLOWED, "https://www.shop.test"]},
            status="active",
        ),
        "off": Channel(
            workspace_id=workspace.id,
            kind="web",
            name="Old",
            webhook_path="off-" + "b" * 29,
            settings_json={"allowed_origins": [ALLOWED]},
            status="disabled",
        ),
        "unconfigured": Channel(
            workspace_id=workspace.id,
            kind="web",
            name="New",
            webhook_path="new-" + "c" * 29,
            settings_json={},
            status="active",
        ),
    }
    migrated.add_all(channels.values())
    await migrated.commit()
    paths = {name: row.webhook_path for name, row in channels.items()}

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://localhost") as http:
            yield http, paths


async def test_the_script_needs_no_session_and_names_no_channel(stage) -> None:
    http, _ = stage
    answer = await http.get("/embed.js")
    assert answer.status_code == 200
    assert "javascript" in answer.headers["content-type"]

    body = answer.text
    # The same file for every channel, which is why it can be cached.
    assert "data-tel-agent" in body
    assert "iframe" in body
    assert "max-age" in answer.headers.get("cache-control", "")


async def test_the_script_does_nothing_it_does_not_need_to(stage) -> None:
    """A script on somebody else's site is a liability they took on trust."""
    http, _ = stage
    body = (await http.get("/embed.js")).text

    for forbidden in ("document.cookie", "localStorage", "XMLHttpRequest", "navigator."):
        assert forbidden not in body, forbidden
    # It reads its own tag and nothing else on the page.
    assert "document.currentScript" in body
    # And the only message it listens to is checked against the frame it created.
    assert "event.source !== frame.contentWindow" in body


async def test_the_page_carries_the_allowlist_as_frame_ancestors(stage) -> None:
    """The guard that actually decides who may embed, and cannot be forged."""
    http, paths = stage
    answer = await http.get(f"/widget/{paths['live']}")
    assert answer.status_code == 200

    csp = answer.headers["content-security-policy"]
    assert f"frame-ancestors {ALLOWED} https://www.shop.test" in csp
    # It loads nothing from anywhere, and talks only back here.
    assert "default-src 'none'" in csp
    assert "connect-src 'self'" in csp


@pytest.mark.parametrize("which", ["off", "unconfigured"])
async def test_a_closed_channel_cannot_be_framed_anywhere(stage, which) -> None:
    http, paths = stage
    answer = await http.get(f"/widget/{paths[which]}")
    assert "frame-ancestors 'none'" in answer.headers["content-security-policy"]
    # And there is no chat in it to run.
    assert 'id="form"' not in answer.text


async def test_an_unknown_address_answers_like_a_closed_one(stage) -> None:
    """The address must not tell a stranger whether a business runs this."""
    http, paths = stage
    unknown = await http.get("/widget/no-such-address-at-all")
    closed = await http.get(f"/widget/{paths['off']}")

    assert unknown.status_code == closed.status_code == 200
    assert unknown.text == closed.text
    assert (
        unknown.headers["content-security-policy"] == closed.headers["content-security-policy"]
    )


async def test_the_page_is_never_cached(stage) -> None:
    """Its policy is per channel, so anything in between must not reuse it."""
    http, paths = stage
    answer = await http.get(f"/widget/{paths['live']}")
    assert answer.headers["cache-control"] == "no-store"


async def test_the_page_posts_to_its_own_channel(stage) -> None:
    http, paths = stage
    body = (await http.get(f"/widget/{paths['live']}")).text
    # The address is embedded as JSON, so a path with a quote in it cannot end the
    # string and start being code.
    assert f"var PATH = {json.dumps(paths['live'])};" in body
    assert "/public/chat/" in body


async def test_the_page_renders_what_a_visitor_typed_as_text(stage) -> None:
    """A chat that renders its own input as markup is an XSS in every host page."""
    http, paths = stage
    body = (await http.get(f"/widget/{paths['live']}")).text
    assert "textContent" in body
    # Not a plain `not in`: the file names `innerHTML` in the comment that says why it
    # is not used, and a test that fails on its own explanation teaches people to
    # delete explanations.
    code = [line for line in body.splitlines() if not line.strip().startswith("//")]
    assert "innerHTML" not in "\n".join(code)


async def test_the_widget_can_reach_the_endpoint_it_points_at(stage) -> None:
    """End to end, as the iframe does it: same origin, no session, no cookie.

    This is the case the origin guard originally refused - the browser stamps the
    iframe's own origin on the message, not the site it is embedded in.
    """
    http, paths = stage
    answer = await http.post(
        f"/public/chat/{paths['live']}/messages",
        json={"text": "Do you open on Saturday?"},
        headers={"Origin": "http://localhost"},
    )
    assert answer.status_code == 201
