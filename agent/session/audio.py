"""The audio bridge — a room's frames on one side, STT and TTS on the other.

Milestone 11's transport has two halves. This is the half that carries no vendor: it
turns a media room (LiveKit, or a raw SIP stream) into the `CallTransport` the archive
side (`api.channels.phone.run_call`) consumes, by wiring the caller's inbound audio
into a speech-to-text stream and the agent's synthesised speech back out. The other
half — actually joining a LiveKit room and moving RTP — is `livekit_room.py`, and it is
the only part that cannot be proven without a real account.

**The seam is `RoomAudio`, and it is deliberately tiny.** Inbound frames in, outbound
frames out, and a flush for barge-in. Everything vendor-specific — connecting,
subscribing to a track, resampling, publishing — lives behind it, so this bridge and
everything above it is exercised with a fake room that carries plain bytes. That is the
same line every channel draws between "what happened in the conversation" and "which
wire it arrived on".

**This bridge satisfies `CallTransport` structurally** (`from_e164`, `tts`, `sink`,
`transcripts()`), so `run_call` drives it exactly as it drives the scripted transport
in the tests — the real one is not a special case. `agent/` owns it because it is audio
and providers, not storage; `run_call`, which has the database, stays in `api/`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Protocol

from agent.providers.stt import STTProvider
from agent.providers.stt.base import Transcript
from agent.providers.tts import TTSProvider

logger = logging.getLogger("agent.session")


class RoomAudio(Protocol):
    """A live call's audio, reduced to what the bridge needs of any media transport.

    `from_e164` is the caller's number when the transport knows it (a SIP call does; a
    web-RTC test may not). `inbound` is the caller's audio as it arrives; `play` sends
    one frame back; `flush` drops whatever is queued for playback but not yet heard,
    which is the barge-in act a provider cannot perform because it never sees the queue.
    The frame bytes are in the codec both sides agreed on out of band — μ-law for a SIP
    stream, PCM for a LiveKit room — and the STT/TTS are configured to match.
    """

    from_e164: str | None

    def inbound(self) -> AsyncIterator[bytes]:
        """The caller's audio frames, until the call ends and the stream closes."""
        ...

    async def play(self, frame: bytes) -> None:
        """Send one audio frame to the caller."""
        ...

    async def flush(self) -> None:
        """Drop queued-but-unplayed audio. Called on barge-in."""
        ...


class _RoomSink:
    """An `AudioSink` (see `agent.session.turn`) backed by a room's playback side."""

    def __init__(self, room: RoomAudio) -> None:
        self._room = room

    async def write(self, chunk: bytes) -> None:
        await self._room.play(chunk)

    async def flush(self) -> None:
        await self._room.flush()


class CallAudioBridge:
    """A `CallTransport` over a `RoomAudio`, speaking through the given STT and TTS.

    The bridge holds no state of its own: `transcripts()` is the caller's inbound audio
    piped straight into the recogniser, and the sink is the room's playback. Everything
    that makes a call a call - turn-taking, barge-in, storage - is `run_call`'s, and it
    sees only this small surface.
    """

    def __init__(self, room: RoomAudio, *, stt: STTProvider, tts: TTSProvider) -> None:
        self.from_e164 = room.from_e164
        self.tts = tts
        self.sink = _RoomSink(room)
        self._room = room
        self._stt = stt

    def transcripts(self) -> AsyncIterator[Transcript]:
        return self._stt.stream(self._room.inbound())
