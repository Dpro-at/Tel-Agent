"""The LiveKit half of the transport — the one part no fake can finish.

This fills the `RoomAudio` seam `audio.py` defines, using `livekit.rtc`: it joins a
room, reads the caller's audio track as PCM frames, and publishes the agent's speech
back. Per the locked decision, a provider number points at a LiveKit Cloud SIP trunk
and LiveKit bridges the call into a room; the agent is a participant that never touches
SIP or RTP directly.

**This module is NOT proven, and it says so in the open.** Every other piece of
Milestone 11 is exercised against a stand-in — the voice core, the providers, the
archive side, and the bridge above. This one cannot be: its whole job is to move real
audio through a real LiveKit connection, and a fake room proves the bridge, not this.
It is written against the documented `livekit-rtc` API so that when the account and the
number exist it is a *verify*, not a *write* — but until a call has actually rung, treat
every frame size, sample rate and resample here as a hypothesis. Rule 2 is explicit:
confirm the call arrives in the provider console before trusting any of this code.

**`livekit` is an optional dependency.** The core installs without it (the bridge and
everything above need only plain bytes), and this module imports it lazily so the test
suite and a text-only installation never require the SDK. Install it with the `voice`
extra when wiring the phone.

**Codec.** LiveKit carries 16-bit PCM at the room's sample rate, not the μ-law a raw
SIP stream uses, so the providers are re-coded to `linear16` at that rate (see
`agent.config._replace_codec` and `ElevenLabs`'s `pcm_*` output). No μ-law transcode
happens on the LiveKit path; the μ-law defaults are for the direct-SIP path instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

logger = logging.getLogger("agent.session.livekit")

# What ElevenLabs streams and what Deepgram is told, for a room at this rate. LiveKit's
# default publish rate; a trunk may negotiate another, in which case these follow it.
ROOM_SAMPLE_RATE = 48000
STT_ENCODING = "linear16"


class LiveKitRoom:
    """A `RoomAudio` backed by a LiveKit room. Unproven until a real call rings.

    Construct it with a connected `rtc.Room`, the caller's number (from the SIP
    participant's attributes), and an `rtc.AudioSource` already publishing a track. The
    worker that builds this is where the connection and token live; keeping them out of
    here is what lets the frame-moving logic be read on its own.
    """

    def __init__(
        self, room, source, *, from_e164, sample_rate: int = ROOM_SAMPLE_RATE  # noqa: ANN001
    ) -> None:
        self.from_e164 = from_e164
        self._room = room
        self._source = source
        self._sample_rate = sample_rate
        self._inbound: asyncio.Queue[bytes | None] = asyncio.Queue()
        # Held so the drain task is not garbage-collected mid-call (RUF006).
        self._drain: asyncio.Task | None = None

    def attach(self, track) -> None:  # noqa: ANN001
        """Start draining the caller's subscribed audio track into the inbound queue.

        Called from the room's `track_subscribed` handler. `rtc.AudioStream` yields
        `rtc.AudioFrame`s; `frame.data` is the PCM samples as bytes, which is exactly
        what Deepgram wants at `linear16`.
        """
        from livekit import rtc

        stream = rtc.AudioStream(track)

        async def drain() -> None:
            try:
                async for event in stream:
                    await self._inbound.put(bytes(event.frame.data))
            finally:
                await self._inbound.put(None)

        self._drain = asyncio.ensure_future(drain())

    async def inbound(self) -> AsyncIterator[bytes]:
        while True:
            frame = await self._inbound.get()
            if frame is None:
                return
            yield frame

    async def play(self, frame: bytes) -> None:
        """Push one synthesised PCM frame to the room's audio source.

        `capture_frame` applies its own playout pacing, which is what makes the audio
        arrive at the caller in real time rather than all at once.
        """
        from livekit import rtc

        audio_frame = rtc.AudioFrame(
            data=frame,
            sample_rate=self._sample_rate,
            num_channels=1,
            samples_per_channel=len(frame) // 2,  # 16-bit samples
        )
        await self._source.capture_frame(audio_frame)

    async def flush(self) -> None:
        """Drop queued playout on barge-in.

        `AudioSource.clear_queue()` exists for exactly this; guarded because an older
        SDK may not have it, and losing the flush is a bug to see rather than to hide.
        """
        clear = getattr(self._source, "clear_queue", None)
        if clear is None:
            logger.warning("this livekit AudioSource has no clear_queue; barge-in cannot flush")
            return
        result = clear()
        if asyncio.iscoroutine(result):
            await result


async def hang_up(room) -> None:  # noqa: ANN001
    """Disconnect from the room, ignoring an already-closed connection."""
    with contextlib.suppress(Exception):
        await room.disconnect()
