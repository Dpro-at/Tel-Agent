"""Milestone 11, the api/ side — a phone call is a conversation in the same archive.

§B5 decision 6: a call is a conversation on a `phone` channel plus a `calls` row for
what only a call has. This drives the voice core (`agent/session/turn.py`) through a
**scripted transport** — no SIP, no LiveKit, no number, no provider key, no cost — and
asserts the thing the live line will later confirm as roadmap check 6: the call lands
beside the chats, searchable, with a transcript whose caller lines carry their STT
confidence and language.

The transport boundary is the transcript, not the audio: the SIP/LiveKit transport
owns codec↔STT↔TTS and hands this driver a stream of `Partial`/`Final`s and a sink to
speak into. That is the seam the real transport implements next; here a list of
scripted transcripts stands in for a caller.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.providers.llm.base import TextDelta
from agent.providers.stt.base import Final, Partial, Transcript
from api.channels import phone
from api.config import Settings
from api.main import create_app
from api.models import (
    Call,
    Channel,
    Conversation,
    Membership,
    Message,
    Notification,
    Rule,
    User,
    Workspace,
)
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105
KEY_HEX = "aa" * 32


@pytest.fixture(autouse=True)
def configured_key(monkeypatch: pytest.MonkeyPatch):
    from api.config import get_settings
    from api.models.encrypted import reset_key_cache

    monkeypatch.setenv("ENCRYPTION_KEY", KEY_HEX)
    get_settings.cache_clear()
    reset_key_cache()
    yield
    get_settings.cache_clear()
    reset_key_cache()


class ScriptedLLM:
    """Answers each turn with a fixed line, so a call has predictable transcript text."""

    def __init__(self, *lines: str) -> None:
        self.lines = list(lines)
        self.turn = 0

    async def stream(self, messages, tools):
        line = self.lines[min(self.turn, len(self.lines) - 1)]
        self.turn += 1
        for word in line.split():
            yield TextDelta(text=word + " ")


class FakeTTS:
    def stream(self, text: str) -> AsyncIterator[bytes]:
        async def gen() -> AsyncIterator[bytes]:
            for word in text.split():
                # A real suspension point between chunks, so the caller talking over
                # the agent (the transport's next transcript) can actually land mid-word.
                await asyncio.sleep(0.005)
                yield word.encode()

        return gen()


class RecordingSink:
    def __init__(self) -> None:
        self.heard: list[bytes] = []
        self.flushes = 0

    async def write(self, chunk: bytes) -> None:
        self.heard.append(chunk)

    async def flush(self) -> None:
        self.flushes += 1


class ScriptedTransport:
    """A caller, as a list of transcripts. `after_speaking` events fire once the agent
    has produced some audio, so a barge-in can be timed to land mid-answer."""

    def __init__(
        self,
        transcripts: list[Transcript],
        *,
        llm,
        from_e164: str | None = "+436761234567",
        barge_after: int | None = None,
    ) -> None:
        self._transcripts = transcripts
        self.llm = llm
        self.tts = FakeTTS()
        self.sink = RecordingSink()
        self.from_e164 = from_e164
        self._barge_after = barge_after

    async def transcripts(self) -> AsyncIterator[Transcript]:
        for index, transcript in enumerate(self._transcripts):
            # A barge-in transcript waits until the agent is mid-answer, so the race in
            # the driver is real rather than order-of-scheduling luck.
            if self._barge_after is not None and index == self._barge_after:
                while len(self.sink.heard) < 2:  # noqa: ASYNC110 - test spin on sink
                    await asyncio.sleep(0)
            yield transcript
            await asyncio.sleep(0)


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    workspace = Workspace(name="Wagner & Partner")
    migrated.add(workspace)
    await migrated.flush()
    channel = Channel(workspace_id=workspace.id, kind="phone", name="Phone", status="active")
    migrated.add(channel)
    user = User(username="mohamed", password_hash=hash_password(PASSWORD))
    migrated.add(user)
    await migrated.flush()
    migrated.add(Membership(user_id=user.id, workspace_id=workspace.id, role="admin"))
    await migrated.commit()

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    async with app.router.lifespan_context(app):
        http = AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://localhost",
        )
        assert (
            await http.post(
                "/api/auth/login", json={"username": "mohamed", "password": PASSWORD}
            )
        ).status_code == 200
        try:
            yield http, app, channel.id, workspace.id, migrated
        finally:
            await http.aclose()


async def _messages(db: AsyncSession, conversation_id: int) -> list[Message]:
    db.expire_all()
    rows = (
        await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.ts_ms, Message.id)
        )
    ).scalars()
    return list(rows)


# --- The full loop, stored in the same archive -----------------------------------


async def test_a_call_becomes_a_conversation_with_a_calls_row_and_a_transcript(stage) -> None:
    _http, app, channel_id, workspace_id, db = stage
    transport = ScriptedTransport(
        [
            Final(text="Are you open on Saturday?", confidence=0.93, language="de"),
            Final(text="Thank you, goodbye.", confidence=0.88, language="de"),
        ],
        llm=ScriptedLLM("Yes, we are open until noon.", "You are welcome. Goodbye."),
    )

    conversation_id = await phone.run_call(
        app.state.sessionmaker,
        channel_id=channel_id,
        transport=transport,
        provider=transport.llm,
    )

    # A conversation on the phone channel, closed when the call ended.
    convo = await db.scalar(select(Conversation).where(Conversation.id == conversation_id))
    assert convo.workspace_id == workspace_id
    assert convo.direction == "inbound"
    assert convo.status == "closed"
    assert convo.ended_at is not None

    # The calls row - what only a call has (§B5 decision 6).
    call = await db.scalar(select(Call).where(Call.conversation_id == conversation_id))
    assert call is not None
    assert call.from_e164 == "+436761234567"
    assert call.billable_seconds is not None and call.billable_seconds >= 0

    # The transcript: caller and agent, in order, caller lines carrying stt fields.
    rows = await _messages(db, conversation_id)
    assert [(r.speaker, r.text) for r in rows] == [
        ("caller", "Are you open on Saturday?"),
        ("agent", "Yes, we are open until noon."),
        ("caller", "Thank you, goodbye."),
        ("agent", "You are welcome. Goodbye."),
    ]
    caller_lines = [r for r in rows if r.speaker == "caller"]
    assert caller_lines[0].stt_confidence == 0.93
    assert caller_lines[0].language == "de"
    # An agent line is spoken, not heard: no STT fields.
    agent_lines = [r for r in rows if r.speaker == "agent"]
    assert agent_lines[0].stt_confidence is None


async def test_the_call_is_findable_in_the_same_search_as_a_chat(stage) -> None:
    http, app, channel_id, _, _db = stage
    transport = ScriptedTransport(
        [Final(text="Do you deliver to Salzburg?", confidence=0.9, language="de")],
        llm=ScriptedLLM("Yes, we deliver across Austria."),
    )
    await phone.run_call(
        app.state.sessionmaker,
        channel_id=channel_id,
        transport=transport,
        provider=transport.llm,
    )

    # Search is the `q` filter on the same conversations list every channel's chats
    # appear in - the call is not a second archive.
    found = await http.get("/api/conversations", params={"q": "Salzburg"})
    assert found.status_code == 200, found.text
    threads = found.json()["threads"]
    assert len(threads) == 1
    assert threads[0]["channel"] == "phone"
    assert threads[0]["is_call"] is True
    assert threads[0]["who"] == "+436761234567"


# --- Partials are not stored -----------------------------------------------------


async def test_a_partial_never_becomes_a_transcript_line(stage) -> None:
    _http, app, channel_id, _, db = stage
    transport = ScriptedTransport(
        [
            Partial(text="Are you"),
            Partial(text="Are you open"),
            Final(text="Are you open today?", confidence=0.91, language="en"),
        ],
        llm=ScriptedLLM("Yes, until six."),
    )
    conversation_id = await phone.run_call(
        app.state.sessionmaker,
        channel_id=channel_id,
        transport=transport,
        provider=transport.llm,
    )
    rows = await _messages(db, conversation_id)
    assert [(r.speaker, r.text) for r in rows] == [
        ("caller", "Are you open today?"),
        ("agent", "Yes, until six."),
    ]


# --- Barge-in truncates the stored agent line ------------------------------------


async def test_barge_in_stops_the_answer_at_a_sentence_it_had_not_begun(stage) -> None:
    """The caller cuts in mid-answer. A phrase already begun was heard and is kept; a
    sentence the answer had not reached is never stored - that line would be the archive
    lying. The cut is at phrase granularity, not mid-word: audio is what was heard, and
    there is no honest way to map half a spoken phrase back to text.
    """
    _http, app, channel_id, _, db = stage
    transport = ScriptedTransport(
        [
            Final(text="Tell me everything.", confidence=0.9, language="en"),
            # Lands once the agent is a couple of chunks into its multi-sentence answer.
            Final(text="Actually, never mind.", confidence=0.9, language="en"),
        ],
        llm=ScriptedLLM(
            "First point here. Second point here. Third point here. Final point here. ",
            "No problem at all.",
        ),
        barge_after=1,
    )
    conversation_id = await phone.run_call(
        app.state.sessionmaker,
        channel_id=channel_id,
        transport=transport,
        provider=transport.llm,
    )
    rows = await _messages(db, conversation_id)
    # Both caller turns landed, each answered.
    assert [r.speaker for r in rows].count("caller") == 2
    first_agent = next(r for r in rows if r.speaker == "agent")
    # A sentence the answer never reached is absent; one it had begun is present.
    assert "First point here." in first_agent.text
    assert "Final point here." not in first_agent.text


# --- Routing: block and pass on the caller ID ------------------------------------


async def test_a_blocked_number_is_not_answered(stage) -> None:
    _http, app, channel_id, workspace_id, db = stage
    db.add(Rule(workspace_id=workspace_id, pattern="+43900*", action="block"))
    await db.commit()
    transport = ScriptedTransport(
        [Final(text="Let me in.", confidence=0.9, language="en")],
        llm=ScriptedLLM("never reached"),
        from_e164="+43900555",
    )
    conversation_id = await phone.run_call(
        app.state.sessionmaker,
        channel_id=channel_id,
        transport=transport,
        provider=transport.llm,
    )
    assert conversation_id is None
    # Nothing stored, nothing spoken.
    assert await db.scalar(select(Conversation)) is None
    assert transport.sink.heard == []


async def test_a_pass_number_reaches_a_person_and_the_agent_stays_silent(stage) -> None:
    _http, app, channel_id, workspace_id, db = stage
    db.add(Rule(workspace_id=workspace_id, pattern="+436761234567", action="pass", note="VIP"))
    await db.commit()
    transport = ScriptedTransport(
        [Final(text="It is the boss.", confidence=0.9, language="en")],
        llm=ScriptedLLM("should not be spoken"),
    )
    conversation_id = await phone.run_call(
        app.state.sessionmaker,
        channel_id=channel_id,
        transport=transport,
        provider=transport.llm,
    )
    convo = await db.scalar(select(Conversation).where(Conversation.id == conversation_id))
    assert convo.handling == "human"
    # The caller's words are recorded; the agent said nothing.
    rows = await _messages(db, conversation_id)
    assert [r.speaker for r in rows] == ["caller"]
    tray = (await db.execute(select(Notification))).scalars().all()
    assert [t.message_key for t in tray] == ["routed_to_person"]
