"""Account events, recorded as they happen — because "did somebody get in" cannot be
answered retroactively.

One append-only table. Rows are written by `api/security/audit.py` and read by the
settings tab; nothing updates or deletes them from the application.

**No secret ever lands here.** Not a password, not a code, not a signature, not a
session token. The test suite greps the recorded rows for exactly that.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base
from api.models.common import utc_now_column

# The vocabulary, closed on purpose: a query over free-text event names degrades into
# guessing what past spellings were. Anything new is added here first.
EVENTS = (
    "login_succeeded",
    "login_failed",
    "login_locked",
    "logout",
    "logout_all",
    "password_changed",
    "password_reset",
    "second_factor_used",
    "key_sign_in_succeeded",
    "key_sign_in_failed",
    "recovery_code_requested",
    # The workspaces epic. Written against the *affected* account, not the acting one:
    # "your role here changed" and "you were removed" are facts about the person they
    # happened to, and the settings tab shows each person their own trail. Who did it
    # goes in `details`.
    "role_changed",
    "member_removed",
    "workspace_created",
    "invite_created",
    "invite_accepted",
    # P7. Not account events in the narrow sense, and recorded here anyway: one backup
    # archive is every transcript on the installation, so downloading one is a data
    # export, and staging a restore is the only action in the product that deletes
    # everything since a date. Both need a name attached to them afterwards.
    "backup_downloaded",
    "backup_deleted",
    "restore_staged",
    # The numbers registry. A number is how customers reach the business, so adding
    # one, disabling one, and above all releasing one need a name attached to them
    # afterwards. Recorded against the acting account.
    "number_added",
    "number_status_changed",
    "number_released",
    # Routing rules. Blocking a caller, or unblocking one, changes who can reach the
    # business - a fact worth a name afterwards. Recorded against the acting account.
    "rule_added",
    "rule_changed",
    "rule_removed",
    # Assistants. The persona and the instructions are what the business says to its
    # customers through the agent, so changing them is a change to how the company
    # speaks - worth a name afterwards. Recorded against the acting account.
    "assistant_added",
    "assistant_changed",
    "assistant_removed",
    # Knowledge. What the agent is allowed to read is what it will say back to a
    # customer, so adding or removing a source changes the answers the business
    # gives. Recorded against the acting account.
    "knowledge_added",
    "knowledge_changed",
    "knowledge_removed",
    # The catalogue. A price is quoted to customers by an agent that cannot check it
    # against anything, so who changed one and when is the only record of why a caller
    # was told a number. Same reasoning as knowledge above: this is what the business
    # says, not how the software is configured.
    "service_added",
    "service_changed",
    "service_removed",
    # Webhooks. A URL that receives conversations is an export of them, and the
    # secret that signs it is a credential - so adding one, changing where it points
    # and removing it all need a name attached afterwards.
    "webhook_added",
    "webhook_changed",
    "webhook_removed",
    # The web chat channel. Which sites may embed it and whether it is on decide who
    # can reach the agent at all, and its reCAPTCHA secret is a credential - so a
    # change needs a name attached afterwards.
    "web_channel_changed",
    # The Telegram channel, for the same reasons: the bot token is a credential, and
    # the switch decides whether customers on Telegram reach the agent at all.
    "telegram_channel_changed",
    # The email channel, likewise: the mailbox password is a credential, and the
    # hosts name the customer's mail infrastructure.
    "email_channel_changed",
    # The WhatsApp channel: two credentials from the customer's own Meta
    # application, and the switch decides whether WhatsApp customers reach the
    # agent at all.
    "whatsapp_channel_changed",
    # Messenger and Instagram: the same reasoning, on the same Meta application.
    "messenger_channel_changed",
    "instagram_channel_changed",
    # Discord and Slack close the messaging trio's platform half: bot tokens are
    # credentials, and each switch decides who reaches the agent.
    "discord_channel_changed",
    "slack_channel_changed",
    # Machine tokens — §B9.1. Each one is a credential that opens a path the
    # dashboard session cannot, so minting, rotating and removing one all need a
    # name attached afterwards. The token itself never appears in `details`.
    "machine_token_added",
    "machine_token_rotated",
    "machine_token_removed",
    # First run. The one event that creates an owner out of nothing, on an endpoint
    # that needs no session because there is nobody to be yet. It can only ever happen
    # once, and an installation whose log does not start with it was set up some other
    # way - which is worth being able to see.
    "installation_created",
)


class AuthEvent(Base):
    """One thing that happened to an account."""

    __tablename__ = "auth_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    # Nullable: a failed sign-in against an unknown username has no user row to point
    # at, and that failure is precisely the kind worth recording.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # The name as typed, kept even when `user_id` is set: after an account is deleted
    # the SET NULL above would otherwise erase who the row was about.
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = utc_now_column()
