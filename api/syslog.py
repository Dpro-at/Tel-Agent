"""The recent log, kept so the health screen can show it — P6.

Logs already go to stdout as JSON. That is right for anything collecting them, and
useless to the one person this product is built for: somebody running Tel-Agent on a
machine in their own office, who has a browser and no journal viewer. §B8's health
screen has had a Log panel drawn since the beginning with nothing behind it.

**A bounded ring in memory, not a table.** Three reasons, in order of weight:

* Writing every log line to the database means the act of logging can itself fail, and
  a logger that raises inside an error handler turns one incident into two.
* A log table grows without limit and is the first thing to fill a small installation's
  disk — the same machine that holds the transcripts.
* What this panel is for is the last few minutes: *why is SMTP red right now*. History
  beyond that is what `auth_events` (deliberately durable) and stdout are for.

The cost is stated rather than hidden: **a restart empties it.** For a panel answering
"what is wrong at this moment" that is acceptable, and for anything else this is the
wrong source.

Everything here is fed by the same handler that formats the JSON, so a line reaching
the screen has already passed through `SecretRedactionFilter`. The panel cannot show a
secret that the log file would not have shown.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import deque
from dataclasses import dataclass
from typing import Any

# Roughly the last few minutes of a busy installation, and a few hours of a quiet one.
# Bounded so memory is a constant an operator can reason about rather than a leak.
CAPACITY = 500

# The four filters the health screen draws, in its own words: All, Errors, Warnings,
# Calls. "Calls" is not a level - it is the services that carry a conversation.
CALL_SERVICES = frozenset({"agent", "tools", "sip", "stt", "tts"})


@dataclass(frozen=True)
class Entry:
    """One line, in the shape the screen already renders."""

    time: str
    level: str
    service: str
    message: str
    request_id: str | None

    def as_json(self) -> dict[str, Any]:
        return {
            "time": self.time,
            "level": self.level,
            "service": self.service,
            "message": self.message,
            "request_id": self.request_id,
        }


def _service_of(logger_name: str) -> str:
    """`api.access` -> `access`, `api.security.session` -> `security`.

    The screen shows one short word per line, so the second segment is taken rather
    than the full dotted path: `api.security.session` in a narrow column pushes the
    message off the end, which is the part somebody is actually reading.
    """
    parts = logger_name.split(".")
    if len(parts) >= 2 and parts[0] in ("api", "agent"):
        return parts[1]
    return parts[0]


class RecentLogHandler(logging.Handler):
    """Keeps the last `CAPACITY` records, and never lets that break logging.

    `emit` is called from inside other people's error paths. A handler that raises
    there would replace a real failure with a confusing one, so everything is caught -
    the standard library's own contract for handlers, honoured deliberately.
    """

    def __init__(self, capacity: int = CAPACITY) -> None:
        super().__init__()
        self.entries: deque[Entry] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.entries.append(
                Entry(
                    time=dt.datetime.fromtimestamp(record.created, dt.UTC).isoformat(),
                    level=record.levelname.lower(),
                    service=_service_of(record.name),
                    # `getMessage` applies the % arguments; the redaction filter has
                    # already run on the handler chain by this point.
                    message=record.getMessage()[:500],
                    request_id=getattr(record, "request_id", None),
                )
            )
        except Exception:  # pragma: no cover - defensive, per the handler contract
            self.handleError(record)

    def recent(self, *, level: str = "all", limit: int = 100) -> list[Entry]:
        """Newest first, filtered the way the screen's four chips filter.

        `warnings` includes errors on purpose: the chip means "everything at least this
        serious", which is what somebody clicking it wants to see. A version that
        showed warnings *only* would hide the errors underneath them.
        """
        entries = list(self.entries)
        entries.reverse()

        if level == "errors":
            entries = [e for e in entries if e.level in ("error", "critical")]
        elif level == "warnings":
            entries = [e for e in entries if e.level in ("warning", "error", "critical")]
        elif level == "calls":
            entries = [e for e in entries if e.service in CALL_SERVICES]

        return entries[:limit]

    def clear(self) -> None:
        self.entries.clear()
