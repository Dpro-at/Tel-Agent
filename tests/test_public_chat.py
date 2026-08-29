"""The widget's endpoint, which anybody on the internet can call.

§B14: every response must be safe to show a stranger. So the tests that matter are the
ones about what a refusal reveals, and the one that proves a refusal stores nothing -
a guard that answers 403 and writes the row anyway is not a guard.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import Channel, Conversation, Message, Workspace

ALLOWED = "https://shop.test"


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    """Two businesses with a widget each, and one that is switched off."""
    mine = Workspace(name="Wagner & Partner")
    theirs = Workspace(name="Wolf Studio")
    migrated.add_all([mine, theirs])
    await migrated.flush()

    channels = {
        "ours": Channel(
            workspace_id=mine.id,
            kind="web",
            name="Web chat",
            webhook_path="ours-" + "a" * 28,
            settings_json={"allowed_origins": [ALLOWED]},
            status="active",
        ),
        # A neighbour's widget, on a different origin. Its address is as public as ours.
        "theirs": Channel(
            workspace_id=theirs.id,
            kind="web",
            name="Web chat",
            webhook_path="theirs-" + "b" * 26,
            settings_json={"allowed_origins": ["https://wolf.test"]},
            status="active",
        ),
        # Configured and switched off, which must be indistinguishable from absent.
        "off": Channel(
            workspace_id=mine.id,
            kind="web",
            name="Web chat (old)",
            webhook_path="off-" + "c" * 29,
            settings_json={"allowed_origins": [ALLOWED]},
            status="disabled",
        ),
        # Live but never configured. Empty list means closed, not open.
        "unconfigured": Channel(
            workspace_id=mine.id,
            kind="web",
            name="Web chat (new)",
            webhook_path="new-" + "d" * 29,
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
            yield http, paths, migrated


async def _count(db: AsyncSession, model) -> int:
    return await db.scalar(select(func.count()).select_from(model)) or 0


async def test_a_message_from_an_allowed_page_arrives(stage) -> None:
    http, paths, db = stage
    answer = await http.post(
        f"/public/chat/{paths['ours']}/messages",
        json={"text": "Do you open on Saturday?"},
        headers={"Origin": ALLOWED},
    )
    assert answer.status_code == 201
    body = answer.json()

    # Milestone 0 step 1: it reached us and is stored.
    db.expire_all()
    stored = await db.scalar(select(Message).where(Message.id == body["message_id"]))
    assert stored is not None
    assert stored.text == "Do you open on Saturday?"
    assert stored.speaker == "caller"
    # Null on a text channel is the signal §B5 gives it: this line was typed.
    assert stored.language is None


async def test_the_answer_tells_a_stranger_nothing_else(stage) -> None:
    http, paths, _ = stage
    body = (
        await http.post(
            f"/public/chat/{paths['ours']}/messages",
            json={"text": "hello"},
            headers={"Origin": ALLOWED},
        )
    ).json()

    # The thread handle and the id of their own message. Nothing about the business.
    assert set(body) == {"conversation", "message_id"}
    # And the handle is not the row id - that would count the conversations the
    # business has had, and invite trying the one next door.
    assert not body["conversation"].isdigit()
    assert len(body["conversation"]) >= 20


async def test_the_second_message_continues_the_same_thread(stage) -> None:
    http, paths, db = stage
    first = (
        await http.post(
            f"/public/chat/{paths['ours']}/messages",
            json={"text": "hello"},
            headers={"Origin": ALLOWED},
        )
    ).json()
    second = await http.post(
        f"/public/chat/{paths['ours']}/messages",
        json={"text": "and one more thing", "conversation": first["conversation"]},
        headers={"Origin": ALLOWED},
    )
    assert second.status_code == 201
    assert second.json()["conversation"] == first["conversation"]

    db.expire_all()
    assert await _count(db, Conversation) == 1
    assert await _count(db, Message) == 2


async def test_a_thread_handle_cannot_be_carried_to_another_widget(stage) -> None:
    http, paths, db = stage
    ours = (
        await http.post(
            f"/public/chat/{paths['ours']}/messages",
            json={"text": "hello"},
            headers={"Origin": ALLOWED},
        )
    ).json()

    # The neighbour's widget, from the neighbour's own allowed page, holding our handle.
    smuggled = await http.post(
        f"/public/chat/{paths['theirs']}/messages",
        json={"text": "let me in", "conversation": ours["conversation"]},
        headers={"Origin": "https://wolf.test"},
    )
    assert smuggled.status_code == 201
    # A new thread on their side rather than an appearance in ours.
    assert smuggled.json()["conversation"] != ours["conversation"]

    db.expire_all()
    theirs_thread = await db.scalar(
        select(Conversation).where(Conversation.external_id == smuggled.json()["conversation"])
    )
    assert theirs_thread is not None
    assert theirs_thread.workspace_id != 1


@pytest.mark.parametrize(
    ("which", "origin", "because"),
    [
        ("ours", "https://evil.test", "a page that is not on the list"),
        ("ours", None, "no Origin header at all"),
        ("ours", "shop.test", "not a well-formed origin"),
        ("ours", "https://shop.test.evil.test", "the allowed origin as a prefix"),
        ("off", ALLOWED, "a channel that is switched off"),
        ("unconfigured", ALLOWED, "a channel with no allowlist yet"),
    ],
)
async def test_every_refusal_looks_the_same_and_stores_nothing(
    stage, which, origin, because
) -> None:
    http, paths, db = stage
    headers = {} if origin is None else {"Origin": origin}
    answer = await http.post(
        f"/public/chat/{paths[which]}/messages", json={"text": "hello"}, headers=headers
    )

    assert answer.status_code == 403, because
    # One code and one sentence for all of them. A stranger holding an address must not
    # learn from the answer whether the business runs this, or whether it is on.
    assert answer.json()["error"]["code"] == "origin_not_allowed"

    db.expire_all()
    assert await _count(db, Conversation) == 0, because
    assert await _count(db, Message) == 0, because


async def test_an_address_that_is_not_a_widget_answers_like_one_that_is_refused(
    stage,
) -> None:
    http, _, db = stage
    answer = await http.post(
        "/public/chat/no-such-address-at-all/messages",
        json={"text": "hello"},
        headers={"Origin": ALLOWED},
    )
    # Not 404. "Does this business run Tel-Agent" is not a question this endpoint
    # answers, and a different status for a missing channel answers it.
    assert answer.status_code == 403
    assert answer.json()["error"]["code"] == "origin_not_allowed"
    assert await _count(db, Message) == 0


async def test_the_endpoint_needs_no_session(stage) -> None:
    """The whole point, and the thing that makes every test above matter."""
    http, paths, _ = stage
    assert "session" not in http.cookies
    answer = await http.post(
        f"/public/chat/{paths['ours']}/messages",
        json={"text": "hello"},
        headers={"Origin": ALLOWED},
    )
    assert answer.status_code == 201


@pytest.mark.parametrize(
    "text",
    ["", "x" * 4001],
)
async def test_an_empty_or_enormous_message_is_refused_before_the_model_sees_it(
    stage, text
) -> None:
    http, paths, db = stage
    answer = await http.post(
        f"/public/chat/{paths['ours']}/messages",
        json={"text": text},
        headers={"Origin": ALLOWED},
    )
    assert answer.status_code == 422
    assert await _count(db, Message) == 0


async def test_a_flood_from_one_page_is_stopped(stage, monkeypatch) -> None:
    """The ceiling, through the endpoint. §B14 makes it not optional.

    Patched down to three rather than sending six hundred: the number is a judgement
    that lives in `quota.py`, and what this proves is that the endpoint consults it,
    refuses with something a widget can act on, and stops storing.
    """
    import datetime as dt

    from api.security import quota

    http, paths, db = stage
    monkeypatch.setattr(
        quota, "PER_ORIGIN", quota.Limit(count=3, window=dt.timedelta(minutes=5))
    )

    for _ in range(3):
        answer = await http.post(
            f"/public/chat/{paths['ours']}/messages",
            json={"text": "hello"},
            headers={"Origin": ALLOWED},
        )
        assert answer.status_code == 201

    refused = await http.post(
        f"/public/chat/{paths['ours']}/messages",
        json={"text": "and again"},
        headers={"Origin": ALLOWED},
    )
    assert refused.status_code == 429
    # Honest, unlike the origin refusal: this caller already passed the allowlist, so
    # it is a page the business let in, and telling it to wait is how a widget behaves.
    assert refused.json()["error"]["code"] == "too_many_messages"

    db.expire_all()
    assert await _count(db, Message) == 3


async def test_a_refused_flood_is_still_remembered(stage, monkeypatch) -> None:
    """The counter has to survive the refusal, or the next request starts again."""
    import datetime as dt

    from api.models.quota import RateCounter
    from api.security import quota

    http, paths, db = stage
    monkeypatch.setattr(
        quota, "PER_ORIGIN", quota.Limit(count=1, window=dt.timedelta(minutes=5))
    )

    for _ in range(4):
        await http.post(
            f"/public/chat/{paths['ours']}/messages",
            json={"text": "hello"},
            headers={"Origin": ALLOWED},
        )

    db.expire_all()
    rows = (await db.execute(select(RateCounter))).scalars().all()
    assert len(rows) == 1
    # One stored message, and a counter that stayed at its ceiling rather than climbing
    # with every refusal.
    assert await _count(db, Message) == 1
    assert rows[0].count == 1


async def test_a_refused_origin_never_reaches_the_counter(stage) -> None:
    """Counting before the allowlist would let any site exhaust a business's budget."""
    from api.models.quota import RateCounter

    http, paths, db = stage
    for _ in range(5):
        await http.post(
            f"/public/chat/{paths['ours']}/messages",
            json={"text": "hello"},
            headers={"Origin": "https://evil.test"},
        )

    db.expire_all()
    assert (await db.execute(select(RateCounter))).scalars().all() == []
