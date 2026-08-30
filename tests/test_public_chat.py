"""The widget's endpoint, which anybody on the internet can call.

§B14: every response must be safe to show a stranger. So the tests that matter are the
ones about what a refusal reveals, and the one that proves a refusal stores nothing -
a guard that answers 403 and writes the row anyway is not a guard.
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import Channel, Conversation, Message, Workspace

ALLOWED = "https://shop.test"
KEY_HEX = "aa" * 32


@pytest.fixture(autouse=True)
def configured_key(monkeypatch: pytest.MonkeyPatch):
    """The reCAPTCHA secret lives in an encrypted column, so a key is not optional.

    Autouse: the tests that do not touch it are unaffected, and a test that forgot it
    would fail inside an INSERT whose parameter dump carries the value.
    """
    from api.config import get_settings
    from api.models.encrypted import reset_key_cache

    monkeypatch.setenv("ENCRYPTION_KEY", KEY_HEX)
    get_settings.cache_clear()
    reset_key_cache()
    yield
    get_settings.cache_clear()
    reset_key_cache()


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


# --- reCAPTCHA, through the endpoint -----------------------------------------


async def _with_captcha(db, path: str, *, threshold: float | None = None) -> None:
    """Switch reCAPTCHA on for a channel, as the settings screen would."""
    from api.models import Channel

    row = await db.scalar(select(Channel).where(Channel.webhook_path == path))
    assert row is not None
    row.credentials_encrypted = "the-channel-secret"
    settings = dict(row.settings_json or {})
    if threshold is not None:
        settings["recaptcha_threshold"] = threshold
    row.settings_json = settings
    await db.commit()


def _google(monkeypatch, body: dict) -> None:
    import httpx

    from api.security import captcha

    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(lambda _: httpx.Response(200, json=body))
        return original(*args, **kwargs)

    monkeypatch.setattr(captcha.httpx, "AsyncClient", factory)


async def test_a_good_token_gets_through(stage, monkeypatch) -> None:
    from api.security import captcha

    http, paths, db = stage
    await _with_captcha(db, paths["ours"])
    _google(monkeypatch, {"success": True, "score": 0.9, "action": captcha.ACTION})

    answer = await http.post(
        f"/public/chat/{paths['ours']}/messages",
        json={"text": "hello", "captcha": "a-token"},
        headers={"Origin": ALLOWED},
    )
    assert answer.status_code == 201


async def test_a_low_score_is_refused_like_a_wrong_origin(stage, monkeypatch) -> None:
    """Same status, same code, same sentence.

    A bot that learns it was the captcha that refused it is a bot that starts solving
    the captcha instead of going away.
    """
    from api.security import captcha

    http, paths, db = stage
    await _with_captcha(db, paths["ours"])
    _google(monkeypatch, {"success": True, "score": 0.1, "action": captcha.ACTION})

    answer = await http.post(
        f"/public/chat/{paths['ours']}/messages",
        json={"text": "hello", "captcha": "a-token"},
        headers={"Origin": ALLOWED},
    )
    assert answer.status_code == 403
    assert answer.json()["error"]["code"] == "origin_not_allowed"

    db.expire_all()
    assert await _count(db, Message) == 0


async def test_no_token_is_refused_once_it_is_switched_on(stage, monkeypatch) -> None:
    from api.security import captcha

    http, paths, db = stage
    await _with_captcha(db, paths["ours"])
    _google(monkeypatch, {"success": True, "score": 0.9, "action": captcha.ACTION})

    answer = await http.post(
        f"/public/chat/{paths['ours']}/messages",
        json={"text": "hello"},
        headers={"Origin": ALLOWED},
    )
    assert answer.status_code == 403
    assert await _count(db, Message) == 0


async def test_a_channel_without_it_never_calls_google(stage, monkeypatch) -> None:
    """The switched-off case, which is most installations."""
    called: list[bool] = []

    import httpx

    from api.security import captcha

    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        called.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(captcha.httpx, "AsyncClient", factory)

    http, paths, _ = stage
    answer = await http.post(
        f"/public/chat/{paths['ours']}/messages",
        json={"text": "hello"},
        headers={"Origin": ALLOWED},
    )
    assert answer.status_code == 201
    assert called == []


async def test_a_refused_origin_never_costs_a_call_to_google(stage, monkeypatch) -> None:
    """The order of the three checks, made visible.

    The two cheap local ones run first, so a request from a page that was never allowed
    does not also buy a round trip on somebody else's network.
    """
    called: list[bool] = []

    import httpx

    from api.security import captcha

    http, paths, db = stage
    await _with_captcha(db, paths["ours"])
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        called.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(captcha.httpx, "AsyncClient", factory)

    answer = await http.post(
        f"/public/chat/{paths['ours']}/messages",
        json={"text": "hello", "captcha": "a-token"},
        headers={"Origin": "https://evil.test"},
    )
    assert answer.status_code == 403
    assert called == []


# --- Milestone 0 step 2: the reply ------------------------------------------


def _events(body: str) -> list[dict]:
    """The payloads out of an SSE body."""
    import json as _json

    return [
        _json.loads(line[len("data: ") :])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


async def test_the_reply_arrives_in_pieces_and_is_stored_whole(stage) -> None:
    """Rule 3's shape, and the archive's.

    Chunks on the wire so a caller never waits on a whole paragraph; one row in the
    transcript, because a half-written reply is indistinguishable from one the agent
    actually gave.
    """
    from agent.reply import GREETING

    http, paths, db = stage
    first = (
        await http.post(
            f"/public/chat/{paths['ours']}/messages",
            json={"text": "Do you open on Saturday?"},
            headers={"Origin": ALLOWED},
        )
    ).json()

    answer = await http.get(
        f"/public/chat/{paths['ours']}/stream",
        params={"conversation": first["conversation"]},
        headers={"Origin": ALLOWED},
    )
    assert answer.status_code == 200
    assert answer.headers["content-type"].startswith("text/event-stream")
    assert answer.headers["cache-control"] == "no-store"

    events = _events(answer.text)
    deltas = [event["delta"] for event in events if "delta" in event]
    # More than one, or it is not streaming.
    assert len(deltas) > 1
    assert "".join(deltas) == GREETING

    done = [event for event in events if event.get("done")]
    assert len(done) == 1

    db.expire_all()
    stored = await db.scalar(select(Message).where(Message.id == done[0]["message_id"]))
    assert stored is not None
    assert stored.speaker == "agent"
    assert stored.text == GREETING


async def test_the_reply_answers_the_message_that_was_stored(stage) -> None:
    """Not one the caller passed in the query string.

    Otherwise anybody could ask for an answer to text the conversation never held.
    """
    http, paths, db = stage
    first = (
        await http.post(
            f"/public/chat/{paths['ours']}/messages",
            json={"text": "the real question"},
            headers={"Origin": ALLOWED},
        )
    ).json()
    await http.get(
        f"/public/chat/{paths['ours']}/stream",
        params={"conversation": first["conversation"], "text": "a different question"},
        headers={"Origin": ALLOWED},
    )

    db.expire_all()
    rows = (await db.execute(select(Message).order_by(Message.id))).scalars().all()
    assert [row.speaker for row in rows] == ["caller", "agent"]
    assert rows[0].text == "the real question"


async def test_the_second_question_is_asked_with_the_first_still_attached(
    stage, monkeypatch
) -> None:
    """Milestone 0 step 4: the thread holds.

    Asserted on what the model is handed rather than on what it says back. A thread
    that arrives without its own beginning still produces a fluent answer - to the
    wrong question - and no assertion on the reply would catch it.
    """
    from api.routes import public_chat

    asked: list[tuple[str, list]] = []

    async def recording_reply(text: str, *, history=None, **_unused):
        asked.append((text, list(history or [])))
        yield "ja"

    monkeypatch.setattr(public_chat, "generate_reply", recording_reply)

    http, paths, _ = stage

    async def say(text: str, thread: str | None = None) -> str:
        answer = await http.post(
            f"/public/chat/{paths['ours']}/messages",
            json={"text": text, **({"conversation": thread} if thread else {})},
            headers={"Origin": ALLOWED},
        )
        assert answer.status_code == 201
        handle = answer.json()["conversation"]
        await http.get(
            f"/public/chat/{paths['ours']}/stream",
            params={"conversation": handle},
            headers={"Origin": ALLOWED},
        )
        return handle

    thread = await say("seid ihr am Samstag offen?")
    await say("und am Sonntag?", thread)

    # The first question arrived with nothing behind it, which is what an opening line
    # is; the second arrived with the exchange that came before it, in order.
    assert asked[0] == ("seid ihr am Samstag offen?", [])
    second_text, second_history = asked[1]
    assert second_text == "und am Sonntag?"
    assert [(turn["role"], turn["content"]) for turn in second_history] == [
        ("user", "seid ihr am Samstag offen?"),
        ("assistant", "ja"),
    ]


async def test_a_visitor_who_reloads_gets_the_thread_back(stage) -> None:
    """Milestone 0's *the thread survives a page reload*, from the visitor's side.

    Without this the widget comes back empty while the server still holds the thread,
    so the visitor asks again and the agent answers a question it was already asked,
    referring to things no longer on the screen.
    """
    http, paths, _ = stage
    first = (
        await http.post(
            f"/public/chat/{paths['ours']}/messages",
            json={"text": "seid ihr am Samstag offen?"},
            headers={"Origin": ALLOWED},
        )
    ).json()
    await http.get(
        f"/public/chat/{paths['ours']}/stream",
        params={"conversation": first["conversation"]},
        headers={"Origin": ALLOWED},
    )

    answer = await http.get(
        f"/public/chat/{paths['ours']}/messages",
        params={"conversation": first["conversation"]},
        headers={"Origin": ALLOWED},
    )
    assert answer.status_code == 200
    body = answer.json()

    assert body["conversation"] == first["conversation"]
    assert [line["speaker"] for line in body["messages"]] == ["visitor", "agent"]
    assert body["messages"][0]["text"] == "seid ihr am Samstag offen?"
    # Nothing that belongs to this installation rather than to this visitor.
    assert set(body["messages"][0]) == {"speaker", "text", "ts_ms"}


async def test_a_whisper_reaches_the_model_as_a_note_not_as_something_it_said(
    stage, monkeypatch
) -> None:
    """The other half of the whisper: the agent is told, in the right voice.

    Handed over as an assistant turn it reads as a line the agent already gave the
    customer, so the model repeats it or answers around it - and the colleague who
    wrote "tell her the quote still stands" gets neither.
    """
    from api.routes import public_chat

    asked: list[list] = []

    async def recording_reply(text: str, *, history=None, **_unused):
        asked.append(list(history or []))
        yield "ja"

    monkeypatch.setattr(public_chat, "generate_reply", recording_reply)

    http, paths, db = stage
    first = (
        await http.post(
            f"/public/chat/{paths['ours']}/messages",
            json={"text": "ist das Angebot noch gültig?"},
            headers={"Origin": ALLOWED},
        )
    ).json()
    thread = await db.scalar(
        select(Conversation).where(Conversation.external_id == first["conversation"])
    )
    db.add(
        Message(
            workspace_id=thread.workspace_id,
            conversation_id=thread.id,
            ts_ms=int(thread.started_at.timestamp() * 1000) + 1,
            speaker="human",
            text="Das Angebot gilt bis 30. September.",
            is_whisper=True,
        )
    )
    await db.commit()

    # A second question, so the whisper is behind it in the thread.
    await http.post(
        f"/public/chat/{paths['ours']}/messages",
        json={"text": "und der Preis?", "conversation": first["conversation"]},
        headers={"Origin": ALLOWED},
    )
    await http.get(
        f"/public/chat/{paths['ours']}/stream",
        params={"conversation": first["conversation"]},
        headers={"Origin": ALLOWED},
    )

    whisper = [turn for turn in asked[-1] if "30. September" in turn["content"]]
    assert len(whisper) == 1
    assert whisper[0]["role"] == "system"
    assert whisper[0]["content"].startswith("Note from a colleague")


async def test_a_whisper_never_reaches_the_visitor(stage) -> None:
    """The one place an internal note could escape: the visitor's own reload.

    A whisper is a colleague coaching the agent mid-thread - "tell her the quote still
    stands, do not redo it". The customer never saw it on the screen and must not see it
    when the widget asks for the thread back.
    """
    http, paths, db = stage
    first = (
        await http.post(
            f"/public/chat/{paths['ours']}/messages",
            json={"text": "ist das Angebot noch gültig?"},
            headers={"Origin": ALLOWED},
        )
    ).json()

    thread = await db.scalar(
        select(Conversation).where(Conversation.external_id == first["conversation"])
    )
    db.add(
        Message(
            workspace_id=thread.workspace_id,
            conversation_id=thread.id,
            ts_ms=thread.started_at.timestamp() * 1000 + 1,
            speaker="human",
            text="Sag ihr, das Angebot gilt bis 30. September.",
            is_whisper=True,
        )
    )
    await db.commit()

    body = (
        await http.get(
            f"/public/chat/{paths['ours']}/messages",
            params={"conversation": first["conversation"]},
            headers={"Origin": ALLOWED},
        )
    ).json()

    assert [line["text"] for line in body["messages"]] == ["ist das Angebot noch gültig?"]
    assert "30. September" not in json.dumps(body)


async def test_a_thread_is_only_readable_from_a_page_that_may_use_the_chat(stage) -> None:
    http, paths, _ = stage
    first = (
        await http.post(
            f"/public/chat/{paths['ours']}/messages",
            json={"text": "hallo"},
            headers={"Origin": ALLOWED},
        )
    ).json()

    # The neighbour's widget, and a page nobody allowed: the same refusal as everywhere
    # else, and it says nothing about whether the thread exists.
    for path, origin in ((paths["theirs"], ALLOWED), (paths["ours"], "https://evil.test")):
        answer = await http.get(
            f"/public/chat/{path}/messages",
            params={"conversation": first["conversation"]},
            headers={"Origin": origin},
        )
        assert answer.status_code == 403
        assert answer.json()["error"]["code"] == "origin_not_allowed"


async def test_a_visitor_who_leaves_stops_the_generation(stage, monkeypatch) -> None:
    """Milestone 0 step 5, in the half that is not about the wire.

    Stopping the tokens reaching the page is easy; stopping them being *produced* is
    what Rule 3 means, and the difference is invisible from outside - the page looks
    the same either way while the bill and the phone's barge-in do not. So this counts
    what the generator was asked for after the visitor went away.
    """
    from api.routes import public_chat

    produced: list[str] = []

    async def endless_reply(text: str, *, history=None, **_unused):
        for index in range(50):
            produced.append(f"chunk-{index}")
            yield f"chunk-{index} "

    monkeypatch.setattr(public_chat, "generate_reply", endless_reply)
    # The route asks this between chunks. Saying yes after the first one is a visitor
    # closing the tab mid-sentence.
    monkeypatch.setattr(
        public_chat.Request, "is_disconnected", lambda self: _answer_once_then_gone()
    )

    http, paths, db = stage
    first = (
        await http.post(
            f"/public/chat/{paths['ours']}/messages",
            json={"text": "hallo"},
            headers={"Origin": ALLOWED},
        )
    ).json()

    answer = await http.get(
        f"/public/chat/{paths['ours']}/stream",
        params={"conversation": first["conversation"]},
        headers={"Origin": ALLOWED},
    )
    assert answer.status_code == 200

    # One chunk was produced and the generator was never asked for the rest.
    assert len(produced) < 50
    # And nothing was stored: a half-written reply in the archive is indistinguishable
    # from one the agent actually gave.
    db.expire_all()
    speakers = [
        row.speaker
        for row in (await db.execute(select(Message).order_by(Message.id))).scalars().all()
    ]
    assert speakers == ["caller"]


async def _answer_once_then_gone() -> bool:
    """`is_disconnected` for a visitor who left immediately."""
    return True


async def test_the_stream_gives_its_connection_back(stage, settings, database_url) -> None:
    """A reply that streams must cost the pool nothing once it has ended.

    The session on `request.state.db` belongs to `AuthenticationMiddleware`, and the
    middleware has already let go of it by the time a streaming body runs - a
    `BaseHTTPMiddleware` returns the response object, and only then is the body
    consumed. A generator that writes its row through that session therefore takes a
    connection nobody is left to return, and the pool loses one per reply. On SQLite
    that is a warning from the garbage collector; on PostgreSQL it is a live backend
    holding locks, which is what stopped three CI runs dead at the twenty-minute cap.

    The application is built here rather than taken from `stage` so the engine whose
    pool is being counted is reachable without private attributes.
    """
    _, paths, _ = stage

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://localhost") as http:
            first = (
                await http.post(
                    f"/public/chat/{paths['ours']}/messages",
                    json={"text": "Do you open on Saturday?"},
                    headers={"Origin": ALLOWED},
                )
            ).json()

            answer = await http.get(
                f"/public/chat/{paths['ours']}/stream",
                params={"conversation": first["conversation"]},
                headers={"Origin": ALLOWED},
            )
            assert answer.status_code == 200
            assert "done" in answer.text

            assert app.state.engine.sync_engine.pool.checkedout() == 0


@pytest.mark.parametrize(
    ("origin", "conversation", "because"),
    [
        ("https://evil.test", None, "a page that is not on the list"),
        (ALLOWED, "not-a-real-handle", "a thread handle that resolves to nothing"),
    ],
)
async def test_the_stream_is_guarded_like_the_message(
    stage, origin, conversation, because
) -> None:
    http, paths, db = stage
    first = (
        await http.post(
            f"/public/chat/{paths['ours']}/messages",
            json={"text": "hello"},
            headers={"Origin": ALLOWED},
        )
    ).json()

    answer = await http.get(
        f"/public/chat/{paths['ours']}/stream",
        params={"conversation": conversation or first["conversation"]},
        headers={"Origin": origin},
    )
    assert answer.status_code == 403, because
    assert answer.json()["error"]["code"] == "origin_not_allowed"

    db.expire_all()
    # One message: the visitor's. No agent row from a refused stream.
    assert await _count(db, Message) == 1


async def test_a_thread_with_nothing_in_it_gets_no_reply(stage) -> None:
    """There is nothing to answer, and inventing one would be the agent talking first."""
    from api.models import Conversation as Thread

    http, paths, db = stage
    empty = Thread(
        workspace_id=1,
        channel_id=1,
        direction="inbound",
        external_id="empty-thread-handle-000000",
        handling="ai",
        status="open",
    )
    db.add(empty)
    await db.commit()

    answer = await http.get(
        f"/public/chat/{paths['ours']}/stream",
        params={"conversation": "empty-thread-handle-000000"},
        headers={"Origin": ALLOWED},
    )
    assert answer.status_code == 403


async def test_the_stream_works_without_an_origin_header(stage) -> None:
    """The browser found this one, and no test could have.

    `EventSource` issues a GET, and a browser sends no `Origin` on a same-origin GET -
    so the widget's own reply stream arrives with no header at all. The first version
    refused it as "no Origin header" and the chat replied to nobody. httpx sends
    whatever a test tells it to, which is exactly why every test passed.

    A header that *is* present is still checked; the test above covers that.
    """
    http, paths, _ = stage
    first = (
        await http.post(
            f"/public/chat/{paths['ours']}/messages",
            json={"text": "hello"},
            headers={"Origin": ALLOWED},
        )
    ).json()

    answer = await http.get(
        f"/public/chat/{paths['ours']}/stream",
        params={"conversation": first["conversation"]},
        # No Origin, deliberately.
    )
    assert answer.status_code == 200
    assert [event for event in _events(answer.text) if "delta" in event]


async def test_a_handle_from_another_channel_gets_no_stream(stage) -> None:
    """What guards the stream instead of the header: the handle is the capability."""
    http, paths, _ = stage
    ours = (
        await http.post(
            f"/public/chat/{paths['ours']}/messages",
            json={"text": "hello"},
            headers={"Origin": ALLOWED},
        )
    ).json()

    answer = await http.get(
        f"/public/chat/{paths['theirs']}/stream",
        params={"conversation": ours["conversation"]},
    )
    assert answer.status_code == 403


# --- Somebody has to be told ------------------------------------------------


async def test_a_new_thread_tells_the_operator(stage) -> None:
    """Otherwise the widget is a box that swallows messages.

    They are stored and searchable, and nobody knows to look.
    """
    from api.models import Notification

    http, paths, db = stage
    await http.post(
        f"/public/chat/{paths['ours']}/messages",
        json={"text": "  Do you   open on Saturday?  "},
        headers={"Origin": ALLOWED},
    )

    db.expire_all()
    rows = (await db.execute(select(Notification))).scalars().all()
    assert len(rows) == 1
    notice = rows[0]
    assert notice.message_key == "web_chat_started"
    # The visitor's own words, with the whitespace they typed collapsed.
    assert notice.params["preview"] == "Do you open on Saturday?"
    # And it opens the thread rather than making somebody find it.
    assert notice.primary_action == "open_conversation"
    assert notice.conversation_id is not None
    # Waiting, not filed. At step 2 the reply promises the visitor that somebody will
    # read it, and that promise is what a person has to keep.
    assert notice.needs_decision is True


async def test_a_talkative_visitor_is_still_one_arrival(stage) -> None:
    """Five rows in the tray would bury the one that came from somebody else."""
    from api.models import Notification

    http, paths, db = stage
    first = (
        await http.post(
            f"/public/chat/{paths['ours']}/messages",
            json={"text": "hello"},
            headers={"Origin": ALLOWED},
        )
    ).json()
    for line in ("and another thing", "and one more"):
        await http.post(
            f"/public/chat/{paths['ours']}/messages",
            json={"text": line, "conversation": first["conversation"]},
            headers={"Origin": ALLOWED},
        )

    db.expire_all()
    assert await _count(db, Notification) == 1
    assert await _count(db, Message) == 3


async def test_a_refused_message_tells_nobody_anything(stage) -> None:
    """A tray that fills up from refused requests is a denial of service on attention."""
    from api.models import Notification

    http, paths, db = stage
    for _ in range(3):
        await http.post(
            f"/public/chat/{paths['ours']}/messages",
            json={"text": "hello"},
            headers={"Origin": "https://evil.test"},
        )

    db.expire_all()
    assert await _count(db, Notification) == 0


async def test_a_long_message_is_trimmed_for_the_tray(stage) -> None:
    from api.models import Notification
    from api.routes.public_chat import PREVIEW_MAX

    http, paths, db = stage
    await http.post(
        f"/public/chat/{paths['ours']}/messages",
        json={"text": "I have a question about " + "something ordinary " * 20},
        headers={"Origin": ALLOWED},
    )

    db.expire_all()
    notice = (await db.execute(select(Notification))).scalars().one()
    preview = notice.params["preview"]
    assert len(preview) <= PREVIEW_MAX
    # Trimmed, not corrupted: it ends on a word and says that it was cut.
    assert preview.endswith("…")
    assert not preview[:-1].endswith(" ")
