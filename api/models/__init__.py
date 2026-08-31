"""Every model, imported here so that `Base.metadata` is complete.

Alembic compares the database against `Base.metadata`. A model that is never imported
is not in that metadata, and autogenerate does not notice it is missing — it writes a
migration that drops the table instead. So this module imports all of them, and
`alembic/env.py` imports this module.
"""

from api.models.assistant import (
    ASSISTANT_STATUSES,
    ASSISTANT_TEMPLATES,
    ASSISTANT_TOOLS,
    Assistant,
)
from api.models.attempt import SCOPES, AuthAttempt
from api.models.audit import EVENTS, AuthEvent
from api.models.backup import BACKUP_KINDS, BACKUP_STATUSES, Backup
from api.models.catalogue import NAME_MAX, PRICE_MODES, SAYS_MAX, Service
from api.models.common import enum_column, utc_now_column, workspace_fk
from api.models.contact import Contact
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
from api.models.invite import INVITE_LIFETIME, INVITE_ROLES, Invite
from api.models.jobs import JOB_STATUSES, BackgroundJob, ScheduledTask
from api.models.knowledge import CONTENT_MAX, Knowledge
from api.models.machine_token import MACHINE_SCOPES, MachineToken
from api.models.notification import CATEGORIES, Notification
from api.models.quota import RateCounter
from api.models.rule import RULE_ACTIONS, Rule
from api.models.session import Session
from api.models.setting import Setting
from api.models.webhook import WEBHOOK_EVENTS, Webhook

__all__ = [
    "ASSISTANT_STATUSES",
    "ASSISTANT_TEMPLATES",
    "ASSISTANT_TOOLS",
    "BACKUP_KINDS",
    "BACKUP_STATUSES",
    "CATEGORIES",
    "CHANNEL_KINDS",
    "CODE_PURPOSES",
    "CONTENT_MAX",
    "EVENTS",
    "INVITE_LIFETIME",
    "INVITE_ROLES",
    "JOB_STATUSES",
    "MACHINE_SCOPES",
    "NAME_MAX",
    "ORIGINS",
    "PRICE_MODES",
    "ROLES",
    "RULE_ACTIONS",
    "SAYS_MAX",
    "SCOPES",
    "WEBHOOK_EVENTS",
    "App",
    "AppInstall",
    "Assistant",
    "AuthAttempt",
    "AuthCode",
    "AuthEvent",
    "BackgroundJob",
    "Backup",
    "Call",
    "Channel",
    "Contact",
    "Conversation",
    "Invite",
    "KeyChallenge",
    "Knowledge",
    "MachineToken",
    "Membership",
    "Message",
    "Notification",
    "Number",
    "PasswordHistory",
    "RateCounter",
    "Rule",
    "ScheduledTask",
    "Service",
    "Session",
    "Setting",
    "User",
    "UserKey",
    "Webhook",
    "Workspace",
    "enum_column",
    "utc_now_column",
    "workspace_fk",
]
