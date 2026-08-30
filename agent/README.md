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

Milestone 0. The model interface exists and has one implementation behind it - an
OpenAI-compatible `/chat/completions` endpoint, streaming - and `reply.py` is what the
web chat route consumes. `tools/` holds the first built-in tool, `take_message`, which
the model is offered on every turn. `stt/`, `tts/`, `session/` and `routing/` are still
empty: this structure is where that code goes as it grows, not an instruction to build
it all now.

Configuration comes from the environment, in `config.py`. That file exists rather than
importing `api.config` because this package never imports from `api/` - at Milestone 11
the agent is a process joining a room, and it has to be configurable without an API
server existing at all.
