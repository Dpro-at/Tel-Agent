"""Structured logging, and the request id that makes it answerable.

"It was slow for one user" cannot be investigated without a correlation id, and adding
one afterwards means touching every handler again. So it exists from the first
endpoint, not from the first outage.

One line of JSON per record. Any log line written while a request is being served
carries that request's id, without the caller passing it down — that is what the
context variable below is for, and it is why a helper function three layers deep does
not need to know a request exists.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import traceback
from contextvars import ContextVar
from typing import Any

from api.syslog import RecentLogHandler

# Identifies the handler this module installs, so that reconfiguring replaces it rather
# than removing every handler anybody else attached.
_HANDLER_NAME = "telagent.json"
# The in-memory ring the health screen reads. Named like the JSON handler so
# `configure_logging` can replace its own without touching anybody else's.
_RECENT_HANDLER_NAME = "telagent.recent"

# Set by the request-id middleware, read by the log filter. A ContextVar rather than a
# global because concurrent requests share the process: a global would let one request's
# id leak into another's log line, which is worse than having no id at all.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes `logging` puts on every record. Anything outside this set was passed by the
# caller through `extra=` and belongs in the output.
_STANDARD_ATTRIBUTES = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


# Field names whose values are secrets wherever they appear. The most common leak is
# not an attack - it is a debug line or a traceback that helpfully includes a request
# body. Matching is by field name because the values themselves are unknowable.
SECRET_FIELD_NAMES = frozenset(
    {
        "password",
        "current_password",
        "new_password",
        "password_hash",
        "token",
        "token_hash",
        "code",
        "signature",
        "credentials",
        "credentials_encrypted",
        "api_key",
        "secret",
        "encryption_key",
        "smtp_password",
    }
)

_REDACTED = "[redacted]"


def _scrub(value: object) -> object:
    """Replace secret-named fields wherever they occur in a structure."""
    if isinstance(value, dict):
        return {
            key: _REDACTED if str(key).lower() in SECRET_FIELD_NAMES else _scrub(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub(item) for item in value]
    return value


class SecretRedactionFilter(logging.Filter):
    """Strip secret-named fields from every record before it is formatted.

    Covers three routes a secret takes into a log line: an `extra=` field named like
    one, a dict payload carrying one nested inside, and a message string that had one
    interpolated as `password='...'`. It cannot catch a secret pasted raw into a
    message with no field name beside it - the test that posts a credential and greps
    the captured output is what keeps that path honest.
    """

    _INLINE: re.Pattern[str] | None = None

    def filter(self, record: logging.LogRecord) -> bool:
        for key in list(record.__dict__):
            if key.lower() in SECRET_FIELD_NAMES:
                setattr(record, key, _REDACTED)
            else:
                value = record.__dict__[key]
                if isinstance(value, (dict, list)):
                    setattr(record, key, _scrub(value))

        if isinstance(record.msg, str) and record.msg:
            if record.args:
                # Interpolate before redacting, not after. `token: %s` with the
                # credential in `args` would otherwise have its placeholder replaced
                # while the argument stayed behind, and `getMessage()` then raises
                # "not all arguments converted" - which drops the line entirely. The
                # secret is in the argument anyway, so redacting the format string
                # alone would have missed it.
                try:
                    record.msg = record.getMessage()
                except Exception:  # pragma: no cover - a malformed call site
                    return True
                record.args = None
            record.msg = self._redact_inline(record.msg)

        # The traceback too. An exception's own message routinely carries the value
        # that caused it - `InvalidToken: 9999:AAH...` - and neither handler formats
        # the traceback through this filter, so it is formatted here instead. Setting
        # `exc_text` is what makes both of them reuse the redacted version rather than
        # re-deriving it from `exc_info`.
        if record.exc_info and not record.exc_text:
            try:
                record.exc_text = "".join(traceback.format_exception(*record.exc_info))
            except Exception:  # pragma: no cover - a broken exc_info is not worth a crash
                record.exc_text = None
        if record.exc_text:
            record.exc_text = self._redact_inline(record.exc_text)
        return True

    @classmethod
    def _redact_inline(cls, text: str) -> str:
        if cls._INLINE is None:
            names = "|".join(sorted(SECRET_FIELD_NAMES))
            # `password='x'`, `token: abc`, `"api_key": "sk-..."` - the value after a
            # secret-named field is replaced, whatever quoting surrounds it.
            cls._INLINE = re.compile(r"(?i)\b(" + names + r")\b(\s*[=:]\s*[\"']?)([^\"'\s,}]+)")
        return cls._INLINE.sub(r"\g<1>\g<2>" + _REDACTED, text)


class RequestIdFilter(logging.Filter):
    """Attach the current request id to every record, if there is one.

    An id passed explicitly through `extra=` wins. The access line is written after the
    context variable has been reset - the id is no longer in context by then - so
    overwriting it here would stamp `null` onto the one line that most needs an id.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "request_id", None) is None:
            record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """One line of JSON per record.

    Machine-readable from the first line rather than after the first time somebody
    tries to grep a multi-line traceback out of a text log.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id

        # Whatever the call site passed through `extra=` - method, path, status,
        # duration_ms and anything a later task adds.
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRIBUTES and key != "request_id":
                payload[key] = value

        if record.exc_text or record.exc_info:
            # `exc_text` first: the redaction filter fills it in, and re-deriving from
            # `exc_info` here would write the unredacted traceback to stdout.
            payload["exception"] = record.exc_text or self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON handler on the root logger.

    Replaces existing handlers rather than adding to them: uvicorn installs its own,
    and leaving both attached prints every line twice — once as JSON and once not,
    which makes the JSON useless to anything parsing it.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestIdFilter())
    # Redaction runs on the handler, after every logger and before any output, so a
    # module that builds its own logger still cannot write a secret through it.
    handler.addFilter(SecretRedactionFilter())
    handler.set_name(_HANDLER_NAME)

    # The same records the JSON handler formats also land in a bounded ring, so the
    # health screen can show them. Added after the redaction filter, so a line on the
    # screen has already been through it - the panel cannot show a secret the log file
    # would not have shown.
    recent = RecentLogHandler()
    recent.set_name(_RECENT_HANDLER_NAME)
    recent.addFilter(RequestIdFilter())
    recent.addFilter(SecretRedactionFilter())

    root = logging.getLogger()
    # Replace only *our* handler, never the whole list.
    #
    # Wiping `root.handlers` is the obvious way to stop uvicorn printing every line
    # twice, and it silently removes anything else that attached one - an error
    # reporter, a journal handler, or the test framework's capture. Building an
    # application would then disable logging for whatever set it up first.
    ours = (_HANDLER_NAME, _RECENT_HANDLER_NAME)
    for existing in [h for h in root.handlers if h.get_name() in ours]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.addHandler(recent)
    root.setLevel(level)

    # uvicorn's access log duplicates the access line the middleware writes, without a
    # request id. Silence it and keep the one that can be correlated.
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.access").propagate = False
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True


def recent_log_handler() -> RecentLogHandler | None:
    """The ring installed by `configure_logging`, if there is one.

    Looked up rather than held in a module global: two applications built in one
    process (which the test suite does constantly) would otherwise share one ring and
    show each other's lines.
    """
    for handler in logging.getLogger().handlers:
        if handler.get_name() == _RECENT_HANDLER_NAME and isinstance(handler, RecentLogHandler):
            return handler
    return None
