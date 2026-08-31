# api/ — REST and WebSocket

Python + FastAPI. The dashboard's backend, and the project's public API — they are the
same thing. The dashboard consumes this API, so it exists anyway; it is simply made
public and documented.

FastAPI generates the OpenAPI documentation, so the docs cannot drift from the code.

## The rules

**This service never touches audio.** No media, no codecs, no RTP. If something here
needs to know what was said, it reads the transcript the agent wrote.

**It does not call the agent, and the agent does not call it.** The database is the
boundary. Redis carries live session state and the events fanned out to connected
browsers.

**Anything the browser can bypass is not a rule.** Authorisation is enforced here, not
in `web/`.

## Surface

```
GET    /health                     deep check: SIP registration, providers, DB
GET    /api/conversations             list + filter, every channel
GET    /api/conversations/{id}        detail + messages
GET    /api/conversations/search?q=   full-text across every channel
POST   /api/calls/outbound            {to, prompt} — phone only
GET    /api/rules      POST  /api/rules
GET    /api/agents     PATCH /api/agents/{id}
GET    /api/contacts
GET    /api/settings   PATCH /api/settings
POST   /api/providers/test
WS     /ws/conversations/{id}         live transcript / message stream
WS     /ws/conversations/{id}/whisper operator -> agent, mid-conversation
```

**Webhooks out**, each signed with a shared secret:
`call.started` · `call.ended` · `intent.detected` · `message.taken` · `tool.failed` ·
`system.degraded`

**Webhook in:** `POST /hooks/call` — start an outbound call from n8n or anything else.

## Security

- API keys are encrypted at rest and never returned in full to a client after saving
- A password is required on first run; there are no default credentials
- The MCP endpoint needs its own token, separate from the dashboard session, with hard
  limits on calls per hour and allowed destination numbers — an external model that
  can start real calls spends real money
- **`/hooks/…` and `/mcp` are the machine paths**, and each carries its own credential
  rather than a session (§B9.1). They are minted from `/api/tokens`, presented as
  `Authorization: Bearer …`, stored as a SHA-256 and shown in full exactly once. A
  token is good for one path family: presented at the other it is refused with the
  same 401 an unknown one gets, so a leak neither opens the other path nor confirms
  itself. Both families have a per-token request ceiling, and an address that has
  proved nothing has a lower one

## Right now

Empty. This arrives at Milestone 6, after persistence, the UI, the channels and the
routing rules exist.
