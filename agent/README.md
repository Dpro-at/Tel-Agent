# agent/ — the voice agent

Owns everything that happens **while a conversation is in progress**: the model loop,
turn-taking, tool execution, and - from Milestone 11 - SIP and the audio path.

It is built as a soft real-time system rather than a web service even while the only
channel is a chat window, because the channel that decides the shape is the phone. See
`docs/ARCHITECTURE.md`.

## The rule that governs this folder

**Nothing on the call path may block.** Every function in the audio path is `async`.
A synchronous call here is a bug even when it is fast today, because it will not be
fast on the day the network is slow.

**Everything streams.** The first sentence starts speaking while the rest is still
being generated. Never wait for a complete model response before starting speech —
that single decision is the difference between a natural call and an obviously robotic
one.

**Budget: under 800 ms** from the end of caller speech to the first audio out.
STT final ~150 ms · LLM first token ~300 ms · TTS first chunk ~100 ms · network ~250 ms.

## Layout

| Folder | Contents |
|---|---|
| `providers/stt/` | Speech to text. One interface, many implementations. |
| `providers/llm/` | Language model. Streams tokens and tool calls. |
| `providers/tts/` | Text to speech. **Must implement `cancel()`** — on barge-in, audio stops instantly and queued speech is discarded. Without it the agent talks over people and the product feels broken. |
| `session/` | Call lifecycle, turn-taking, barge-in, whisper handling |
| `routing/` | Whitelist / blacklist / business hours; decides pass, block, or AI from the SIP caller ID |
| `tools/` | Built-in tools the model can invoke |

## Not in this folder

The dashboard's REST and WebSocket endpoints. Those are `api/`. The agent writes to
the database and publishes events; it does not serve the UI.

## Right now

The model interface exists and has one implementation behind it - an OpenAI-compatible
`/chat/completions` endpoint, streaming - and `reply.py` is what every channel consumes.
`tools/` holds the built-in tools the model is offered.

Milestone 11's software core is now in place, ahead of the live line it cannot be
closed without: `providers/stt/base.py` and `providers/tts/base.py` are §B3's two
missing interfaces (partials and finals in, audio chunks out, `cancel()` mandatory),
and `session/turn.py` is the turn-taker that speaks a streamed reply through them and
stops the instant the caller cuts in - the barge-in that Rule 3 makes non-optional,
with the end-of-speech-to-first-audio latency Rule 4 logs. All of it runs on fakes in
`tests/test_voice_turn.py`; no provider, no SIP, no key.

The two concrete providers §B3 names for v1 are in place behind those interfaces:
`providers/stt/deepgram.py` (Deepgram's streaming WebSocket — interims as `Partial`s,
`is_final` as `Final`s, μ-law 8 kHz to match a SIP call) and `providers/tts/eleven
labs.py` (ElevenLabs' streaming endpoint, `ulaw_8000` output, cancellation by closing
the response). Both are exercised against stand-ins in `tests/test_voice_providers.py`
— a WebSocket server for Deepgram, an httpx `MockTransport` for ElevenLabs — so no key
and no cost. `config.py` reads their env (`DEEPGRAM_*`, `ELEVENLABS_*`) the same way it
reads the model's, and `configured_stt()`/`configured_tts()` build them.

The transport's codec-free core is built too: `session/audio.py`'s `CallAudioBridge`
turns a media room into the `CallTransport` `run_call` consumes — inbound frames into
`DeepgramSTT`, `ElevenLabsTTS` out through the room — behind a tiny `RoomAudio` seam, so
the whole thing is exercised in `tests/test_audio_bridge.py` with a fake room and the
real providers against their stand-ins: a whole call, frames in to frames out, stored
in the archive.

The one piece no fake can finish is `session/livekit_room.py` — the actual LiveKit
binding that joins a room and moves RTP. It is written against the documented SDK API
but **not proven**, because its whole job is real audio through a real connection; it is
a *verify* when the LiveKit account and the number exist, not a *write* (Rule 2). The
`livekit` SDK is the optional `voice` extra so the core installs without it. `routing/`
is a stub - the rules engine lives at `api/routing.py` (Milestone 4), which the phone
already calls on the caller ID.

Configuration comes from the environment, in `config.py`. That file exists rather than
importing `api.config` because this package never imports from `api/` - at Milestone 11
the agent is a process joining a room, and it has to be configurable without an API
server existing at all.
