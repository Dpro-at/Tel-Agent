"""Milestone 11 — Deepgram and ElevenLabs, against stand-ins for the real services.

§B3's v1 pair, behind the interfaces #144 defined. Neither test needs a real key or a
cent: ElevenLabs is an `httpx.MockTransport` that streams bytes back, and Deepgram is a
real local WebSocket server speaking its JSON protocol — the same "fake the platform,
not the code" approach every channel's tests use. What is under test is the wire
handling: that partials and finals come out in order with their confidence and
language, that audio streams chunk by chunk, that a refusal is raised not swallowed,
and that closing the stream stops the far end (cancellation).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest
import websockets

from agent.config import SttSettings, TtsSettings
from agent.providers.stt.base import Final, Partial
from agent.providers.stt.deepgram import DeepgramSTT
from agent.providers.tts.elevenlabs import ElevenLabsTTS, TTSError


async def _audio(*chunks: bytes) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk
        await asyncio.sleep(0)


# --- ElevenLabs TTS, against an httpx MockTransport ------------------------------


def _tts(handler, **over) -> ElevenLabsTTS:
    settings = TtsSettings(
        api_key="dev-fake-key",
        voice_id="voice-1",
        model="eleven_turbo_v2_5",
        output_format="ulaw_8000",
        base_url="https://tts.test",
        **over,
    )
    return ElevenLabsTTS(settings, transport=httpx.MockTransport(handler))


async def test_tts_streams_audio_chunks_and_sends_the_right_request() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("xi-api-key")
        seen["body"] = json.loads(request.content)
        # Three chunks of "audio", to prove streaming rather than one blob.
        return httpx.Response(200, stream=_ByteStream([b"aud", b"io", b"!!"]))

    tts = _tts(handler)
    chunks = [chunk async for chunk in tts.stream("Guten Tag")]

    assert b"".join(chunks) == b"audio!!"
    assert "/v1/text-to-speech/voice-1/stream" in seen["url"]
    assert "output_format=ulaw_8000" in seen["url"]
    assert seen["key"] == "dev-fake-key"
    assert seen["body"]["text"] == "Guten Tag"
    assert seen["body"]["model_id"] == "eleven_turbo_v2_5"


async def test_tts_raises_on_a_refusal_rather_than_yielding_silence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "invalid key"})

    tts = _tts(handler)
    with pytest.raises(TTSError) as caught:
        async for _ in tts.stream("hallo"):
            pass
    assert "401" in str(caught.value)


async def test_tts_closing_the_stream_early_closes_the_response() -> None:
    """Barge-in: the consumer stops after the first chunk, and the response must close
    so ElevenLabs stops synthesising - the far-end stop the whole design rests on."""
    closed = asyncio.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_ByteStream([b"one", b"two", b"three"], closed))

    tts = _tts(handler)
    gen = tts.stream("a long answer")
    first = await gen.__anext__()
    assert first == b"one"
    await gen.aclose()
    # The generator's finally closed the client, which closed the stream.
    assert closed.is_set()


class _ByteStream(httpx.AsyncByteStream):
    """A streaming body that yields its chunks one at a time and flags when closed."""

    def __init__(self, chunks: list[bytes], closed: asyncio.Event | None = None) -> None:
        self._chunks = chunks
        self._closed = closed

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk
            await asyncio.sleep(0)

    async def aclose(self) -> None:
        if self._closed is not None:
            self._closed.set()


# --- Deepgram STT, against a local WebSocket server -----------------------------


class FakeDeepgram:
    """A WebSocket server that speaks Deepgram's transcript protocol.

    It receives audio frames, and on `CloseStream` (or once enough audio has arrived)
    sends a scripted sequence of interim and final results, then closes - the shape a
    real streaming recognition has, reduced to what the client must parse.
    """

    def __init__(self, script: list[dict]) -> None:
        self._script = script
        self.received: list[bytes] = []
        self.closed_by_client = False

    async def _handler(self, ws) -> None:
        async for message in ws:
            if isinstance(message, bytes):
                self.received.append(message)
                continue
            data = json.loads(message)
            if data.get("type") == "CloseStream":
                self.closed_by_client = True
                break
        for result in self._script:
            await ws.send(json.dumps(result))
        await ws.close()

    async def __aenter__(self) -> str:
        self._server = await websockets.serve(self._handler, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        return f"ws://127.0.0.1:{port}"

    async def __aexit__(self, *exc) -> None:
        self._server.close()
        await self._server.wait_closed()


def _results(transcript: str, *, is_final: bool, confidence: float = 0.9) -> dict:
    return {
        "type": "Results",
        "is_final": is_final,
        "channel": {"alternatives": [{"transcript": transcript, "confidence": confidence}]},
    }


def _stt(base_url: str) -> DeepgramSTT:
    return DeepgramSTT(
        SttSettings(api_key="dev-fake-key", model="nova-2", language="de", base_url=base_url)
    )


async def test_stt_yields_partials_then_a_final_with_confidence_and_language() -> None:
    script = [
        _results("Guten", is_final=False),
        _results("Guten Tag", is_final=False),
        _results("Guten Tag.", is_final=True, confidence=0.95),
        {"type": "Metadata", "duration": 1.2},  # not a transcript; must be ignored
    ]
    async with FakeDeepgram(script) as url:
        stt = _stt(url)
        out = [t async for t in stt.stream(_audio(b"\x00\x01", b"\x02\x03"))]

    assert [type(t) for t in out] == [Partial, Partial, Final]
    assert out[0].text == "Guten"
    assert out[-1].text == "Guten Tag."
    assert out[-1].confidence == 0.95
    assert out[-1].language == "de"


async def test_stt_sends_the_audio_and_then_closes_the_stream() -> None:
    fake = FakeDeepgram([_results("Ja.", is_final=True)])
    async with fake as url:
        stt = _stt(url)
        out = [t async for t in stt.stream(_audio(b"aaaa", b"bbbb"))]

    assert [t.text for t in out] == ["Ja."]
    # The caller's audio arrived, and the client asked Deepgram to flush.
    assert fake.received == [b"aaaa", b"bbbb"]
    assert fake.closed_by_client is True


async def test_stt_an_empty_transcript_is_not_a_line() -> None:
    """A silent frame gets an empty transcript from Deepgram; storing it would be the
    archive inventing a line nobody said."""
    async with FakeDeepgram([_results("", is_final=True)]) as url:
        stt = _stt(url)
        out = [t async for t in stt.stream(_audio(b"\x00\x00"))]
    assert out == []


async def test_stt_the_key_travels_as_an_authorization_token() -> None:
    seen: dict = {}

    async def handler(ws) -> None:
        seen["auth"] = ws.request.headers.get("Authorization")
        await ws.close()

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        stt = _stt(f"ws://127.0.0.1:{port}")
        _ = [t async for t in stt.stream(_audio(b"x"))]
    finally:
        server.close()
        await server.wait_closed()
    assert seen["auth"] == "Token dev-fake-key"
