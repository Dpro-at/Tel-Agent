"""Take a message — Milestone 0 step 6.

The one thing an agent must be able to do on the day it is switched on, before it knows
a single opening hour: find out who is asking and what about, and hand that to a person.
Every other tool is an improvement on this one.

**Two required fields and nothing else.** A form with six boxes is a form the visitor
abandons and a tool the model fills with guesses; a name and a reason are what somebody
ringing back actually needs. `callback` is optional because the channel usually already
knows how to reach them, and `urgent` is optional because the model should not be
deciding it unprompted.

**The result is a value, not a row.** What is done with a taken message - a
notification, a row in the tray, an email - belongs to whoever called the agent, and
differs between a web chat and a phone call at Milestone 11.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from agent.tools.base import Tool, ToolError

logger = logging.getLogger("agent.tools")

# Long enough for "the boiler in the upstairs flat is leaking again since Tuesday" and
# short enough that a model which decides to summarise the whole conversation into this
# field is truncated rather than believed.
REASON_MAX = 500
NAME_MAX = 120
CALLBACK_MAX = 120


@dataclass(frozen=True)
class TakenMessage:
    """Who called, what about, and how to reach them."""

    name: str
    reason: str
    callback: str | None = None
    urgent: bool = False


SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "The caller's name, as they gave it.",
        },
        "reason": {
            "type": "string",
            "description": "What they want, in their own words where possible.",
        },
        "callback": {
            "type": "string",
            "description": (
                "A phone number or email address they gave for the call back. "
                "Omit it rather than inventing one."
            ),
        },
        "urgent": {
            "type": "boolean",
            "description": "Only when they said it cannot wait.",
        },
    },
    "required": ["name", "reason"],
    "additionalProperties": False,
}

DESCRIPTION = (
    "Take a message for a person to answer later. Call this once you know both who is "
    "asking and what about - ask for whichever is missing first, in the visitor's own "
    "language. Do not call it for a question you have already answered, and do not "
    "invent a name: if they will not give one, ask once and then take the message "
    "without it."
)


def _text(arguments: dict[str, Any], field: str, limit: int) -> str:
    value = arguments.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"{field} is required, as text the caller actually gave.")
    return value.strip()[:limit]


def parse(arguments: dict[str, Any]) -> TakenMessage:
    """The model's arguments, or a refusal it can act on.

    Validated here rather than trusted: the arguments are a language model's output,
    which means they are a suggestion. A message stored under a name the model invented
    sends somebody to ring the wrong person while the real one waits.
    """
    callback = arguments.get("callback")
    if callback is not None and not isinstance(callback, str):
        raise ToolError("callback must be a phone number or an email address, as text.")

    return TakenMessage(
        name=_text(arguments, "name", NAME_MAX),
        reason=_text(arguments, "reason", REASON_MAX),
        callback=(callback.strip()[:CALLBACK_MAX] or None)
        if isinstance(callback, str)
        else None,
        urgent=bool(arguments.get("urgent", False)),
    )


async def _run(arguments: dict[str, Any]) -> str:
    """What the model is told once the message is taken.

    Short and certain: a model handed a paragraph here repeats it to the visitor, and
    the visitor is owed a confirmation rather than a receipt.
    """
    taken = parse(arguments)
    # Rule 4's habit, applied to a tool: this is the line that says the step works,
    # printed as a structured record rather than as prose.
    logger.info(
        "message taken",
        extra={
            "caller_name": taken.name,
            "reason": taken.reason,
            "callback": taken.callback,
            "urgent": taken.urgent,
        },
    )
    return "The message was taken. Confirm it briefly and say somebody will come back to them."


TAKE_MESSAGE = Tool(
    name="take_message",
    description=DESCRIPTION,
    parameters=SCHEMA,
    run=_run,
)
