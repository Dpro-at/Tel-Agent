"""ElevenLabs, behind the TTS interface — §B3's v1 text-to-speech.

The streaming endpoint, not the batch one: `POST /v1/text-to-speech/{voice}/stream`
returns audio in chunks as it synthesises, which is what Rule 3 needs — the first
chunk leaves before the last is made, so the first sentence speaks while the rest
generates. The batch endpoint would return one blob at the end, reintroducing exactly
the silence the streaming design exists to remove.

**The output format is a telephony codec, not mp3.** `ulaw_8000` is G.711 μ-law at
8 kHz, which is what a SIP call already carries (SIP_CODEC=PCMU). Asking for mp3 and
transcoding to μ-law on the way to the caller would put a CPU-bound decode on the media
path, which Rule 0's "nothing on the call path may block" forbids. The transport hands
these bytes straight to the room.

**Cancellation is the generator's own close.** The `async with client.stream(...)`
lives inside the generator, so when `agent.session.speak` stops consuming on barge-in,
`GeneratorExit` unwinds the `async with`, the HTTP response closes, and ElevenLabs
stops synthesising — which stops the bill, not just the playback. That is why the
request is opened inside the generator and streamed, never awaited to completion first.

The key rides in `xi-api-key` and is never logged: it is set on the client here and the
log lines below name the voice and the byte count, never the header.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx

from agent.config import TtsSettings

logger = logging.getLogger("agent.tts")

# Tight, because this is on the call path. A synthesiser that has not sent its first
# chunk in this long is one whose silence the caller is already hearing; the transport
# would rather fail the turn than hold the line.
_CONNECT_TIMEOUT = 5.0
_READ_TIMEOUT = 15.0

# ElevenLabs' documented default voice settings, made explicit so a change is a change
# here rather than a drift in their defaults. Stability against expressiveness is a
# per-installation choice that belongs in settings the day somebody asks for it.
_VOICE_SETTINGS = {
    "stability": 0.5,
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": True,
}


class TTSError(RuntimeError):
    """ElevenLabs refused. Carries the status and the start of its body, never the key."""


class ElevenLabsTTS:
    """Streams μ-law audio for the phone. Satisfies `TTSProvider` structurally.

    `transport` is injectable so a test can stand in for ElevenLabs with an
    `httpx.MockTransport`; production leaves it None and a real client is built.
    """

    def __init__(
        self, settings: TtsSettings, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._transport = transport

    def stream(self, text: str) -> AsyncIterator[bytes]:
        settings = self._settings
        transport = self._transport

        async def gen() -> AsyncIterator[bytes]:
            url = (
                f"{settings.base_url}/v1/text-to-speech/{settings.voice_id}/stream"
                f"?output_format={settings.output_format}"
            )
            headers = {"xi-api-key": settings.api_key, "accept": "audio/basic"}
            body = {
                "text": text,
                "model_id": settings.model,
                "voice_settings": _VOICE_SETTINGS,
            }
            timeout = httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT)
            client = httpx.AsyncClient(timeout=timeout, transport=transport)
            try:
                async with client.stream("POST", url, headers=headers, json=body) as response:
                    if response.status_code >= 300:
                        detail = (await response.aread())[:200].decode("utf-8", "replace")
                        raise TTSError(f"ElevenLabs answered {response.status_code}: {detail}")
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            yield chunk
            finally:
                # Closes the connection whether the stream finished or barge-in unwound
                # it, which is what stops synthesis at the far end.
                await client.aclose()

        return gen()
