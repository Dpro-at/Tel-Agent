"""Every model, imported here so that `Base.metadata` is complete.

Alembic compares the database against `Base.metadata`. A model that is never imported
is not in that metadata, and autogenerate does not notice it is missing — it writes a
migration that drops the table instead. So this module imports all of them, and
`alembic/env.py` imports this module.
"""

from api.models.attempt import SCOPES, AuthAttempt
from api.models.audit import EVENTS, AuthEvent
from api.models.common import enum_column, utc_now_column, workspace_fk
from api.models.conversation import (
    CHANNEL_KINDS,
    Call,
    Channel,
    Conversation,
    Message,
    Number,
)
from api.models.credential import (
    CODE_PURPOSES,
    AuthCode,
    KeyChallenge,
    PasswordHistory,
    UserKey,
)
from api.models.extensions import ORIGINS, App, AppInstall
from api.models.identity import ROLES, Membership, User, Workspace
from api.models.session import Session

__all__ = [
    "CHANNEL_KINDS",
    "CODE_PURPOSES",
    "EVENTS",
    "ORIGINS",
    "ROLES",
    "SCOPES",
    "App",
    "AppInstall",
    "AuthAttempt",
    "AuthCode",
    "AuthEvent",
    "Call",
    "Channel",
    "Conversation",
    "KeyChallenge",
    "Membership",
    "Message",
    "Number",
    "PasswordHistory",
    "Session",
    "User",
    "UserKey",
    "Workspace",
    "enum_column",
    "utc_now_column",
    "workspace_fk",
]
