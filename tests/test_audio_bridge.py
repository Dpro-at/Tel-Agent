"""Milestone 11 — the audio bridge, and the whole transport core end to end.

The bridge (`agent.session.CallAudioBridge`) is thin, so its unit tests are about
wiring: the caller's inbound frames reach the recogniser, the agent's audio reaches the
room, and a flush reaches the room. The integration test is the one that matters — it
runs a whole call through `run_call` with the **real** Deepgram and ElevenLabs clients
pointed at stand-ins, so everything except the LiveKit wire is exercised together:
frames in → STT → transcripts → reply → TTS → frames out, stored as a call. The LiveKit
wire itself (`livekit_room.py`) is the one piece no fake can finish.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
import websockets
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.config import SttSettings, TtsSettings
from agent.providers.llm.base import TextDelta
from agent.providers.stt.base import Final, Partial, Transcript
from agent.providers.stt.deepgram import DeepgramSTT
from agent.providers.tts.elevenlabs import ElevenLabsTTS
from agent.session import CallAudioBridge
from api.channels import phone
from api.config import Settings
from api.main import create_app
from api.models import Call, Channel, Conversation, Membership, Message, User, Workspace
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


class FakeRoom:
    """A `RoomAudio` carrying plain bytes: scripted inbound frames, captured outbound."""

    def __init__(self, frames: list[bytes], *, from_e164: str | None = "+436761234567") -> None:
        self.from_e164 = from_e164
        self._frames = frames
        self.played: list[bytes] = []
        self.flushes = 0

    async def inbound(self) -> AsyncIterator[bytes]:
        import asyncio

        for frame in self._frames:
            yield frame
            await asyncio.sleep(0)

    async def play(self, frame: bytes) -> None:
        self.played.append(frame)

    async def flush(self) -> None:
        self.flushes += 1


# --- Bridge wiring ---------------------------------------------------------------


class _RecordingSTT:
    def __init__(self, transcripts: list[Transcript]) -> None:
        self._transcripts = transcripts
        self.consumed: list[bytes] = []

    def stream(self, audio: AsyncIterator[bytes]) -> AsyncIterator[Transcript]:
        async def gen() -> AsyncIterator[Transcript]:
            async for chunk in audio:
                self.consumed.append(chunk)
            for transcript in self._transcripts:
                yield transcript

        return gen()


class _WordTTS:
    def stream(self, text: str) -> AsyncIterator[bytes]:
        async def gen() -> AsyncIterator[bytes]:
            for word in text.split():
                yield word.encode()

        return gen()


async def test_the_bridge_pipes_inbound_audio_into_the_recogniser() -> None:
    room = FakeRoom([b"frame-1", b"frame-2"])
    stt = _RecordingSTT([Partial(text="hi"), Final(text="hi there", confidence=0.9)])
    bridge = CallAudioBridge(room, stt=stt, tts=_WordTTS())

    out = [t async for t in bridge.transcripts()]

    assert stt.consumed == [b"frame-1", b"frame-2"]
    assert [t.text for t in out] == ["hi", "hi there"]
    assert bridge.from_e164 == "+436761234567"


async def test_the_bridge_sink_plays_to_the_room_and_flush_reaches_it() -> None:
    room = FakeRoom([])
    bridge = CallAudioBridge(room, stt=_RecordingSTT([]), tts=_WordTTS())

    await bridge.sink.write(b"hello")
    await bridge.sink.write(b"world")
    await bridge.sink.flush()

    assert room.played == [b"hello", b"world"]
    assert room.flushes == 1


# --- The whole transport core, end to end ----------------------------------------


class _FakeDeepgram:
    """A local WebSocket server speaking Deepgram's protocol, scripted per call."""

    def __init__(self, script: list[dict]) -> None:
        self._script = script

    async def _handler(self, ws) -> None:
        async for message in ws:
            if not isinstance(message, bytes):
                data = json.loads(message)
                if data.get("type") == "CloseStream":
                    break
        for result in self._script:
            await ws.send(json.dumps(result))
        await ws.close()

    async def __aenter__(self) -> str:
        self._server = await websockets.serve(self._handler, "127.0.0.1", 0)
        return f"ws://127.0.0.1:{self._server.sockets[0].getsockname()[1]}"

    async def __aexit__(self, *exc) -> None:
        self._server.close()
        await self._server.wait_closed()


def _final(text: str) -> dict:
    return {
        "type": "Results",
        "is_final": True,
        "channel": {"alternatives": [{"transcript": text, "confidence": 0.92}]},
    }


class _ScriptedLLM:
    def __init__(self, *lines: str) -> None:
        self.lines = list(lines)
        self.turn = 0

    async def stream(self, messages, tools):
        line = self.lines[min(self.turn, len(self.lines) - 1)]
        self.turn += 1
        for word in line.split():
            yield TextDelta(text=word + " ")


def _el_tts() -> ElevenLabsTTS:
    def handler(request: httpx.Request) -> httpx.Response:
        # One byte-chunk per word of the requested text, so "played" audio is checkable.
        text = json.loads(request.content)["text"]
        return httpx.Response(200, content=text.replace(" ", "").encode())

    settings = TtsSettings(
        api_key="dev-fake-key",
        voice_id="voice-1",
        model="eleven_turbo_v2_5",
        output_format="pcm_8000",
        base_url="https://tts.test",
    )
    return ElevenLabsTTS(settings, transport=httpx.MockTransport(handler))


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
            yield app, channel.id, migrated
        finally:
            await http.aclose()


async def test_a_whole_call_runs_through_the_bridge_and_lands_in_the_archive(stage) -> None:
    """Frames in → real Deepgram (stand-in) → transcripts → reply → real ElevenLabs
    (stand-in) → frames out, stored as a conversation with a calls row. Everything but
    the LiveKit wire, exercised at once."""
    app, channel_id, db = stage

    async with _FakeDeepgram([_final("Are you open on Saturday?")]) as dg_url:
        stt = DeepgramSTT(
            SttSettings(
                api_key="dev-fake-key",
                model="nova-2",
                language="de",
                base_url=dg_url,
                encoding="linear16",
                sample_rate=8000,
            )
        )
        room = FakeRoom([b"\x00\x01\x02\x03", b"\x04\x05\x06\x07"])
        bridge = CallAudioBridge(room, stt=stt, tts=_el_tts())

        conversation_id = await phone.run_call(
            app.state.sessionmaker,
            channel_id=channel_id,
            transport=bridge,
            provider=_ScriptedLLM("Yes, we are open until noon."),
        )

    # The caller's audio reached the recogniser, and the agent's speech reached the room.
    assert room.played  # ElevenLabs bytes were captured back into the room
    convo = await db.scalar(select(Conversation).where(Conversation.id == conversation_id))
    assert convo.status == "closed"
    call = await db.scalar(select(Call).where(Call.conversation_id == conversation_id))
    assert call is not None
    rows = (
        (
            await db.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.ts_ms, Message.id)
            )
        )
        .scalars()
        .all()
    )
    assert [(r.speaker, r.text) for r in rows] == [
        ("caller", "Are you open on Saturday?"),
        ("agent", "Yes, we are open until noon."),
    ]
    # The caller line came from Deepgram, so it carries the confidence Deepgram gave.
    assert rows[0].stt_confidence == 0.92
