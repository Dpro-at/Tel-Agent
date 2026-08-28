"""Sending email, and being honest when this installation cannot.

Most Tel-Agent installations have no mail server. That is not a broken state to hide —
the `forgot` screen has a designed answer for it:

> "This installation cannot send email. No mail server is configured, so there is
> nowhere to send a code. An administrator resets the password on the machine itself."

So `is_configured()` is checked before a code is ever issued, and the API says
`unavailable` rather than claiming a message is on its way that will never arrive.
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import TYPE_CHECKING

from api.config import Settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession as DbSession

logger = logging.getLogger("api.mail")


def is_configured(settings: Settings) -> bool:
    """Can this installation send at all? (environment only — see `resolve`.)"""
    return bool(settings.smtp_host and settings.smtp_from)


@dataclass(frozen=True)
class MailConfig:
    """The mail server this installation will actually use."""

    host: str | None
    port: int
    username: str | None
    password: str | None
    sender: str | None
    use_tls: bool
    use_ssl: bool

    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender)


async def resolve(db: DbSession, settings: Settings) -> MailConfig:
    """The store first, the environment second.

    The order matters and it is the point of P3: `.env` is what an installer wrote
    once, the store is what the owner changed from the settings screen afterwards. A
    value set in the screen that lost to a stale environment variable would be a
    setting that appears to save and does nothing.
    """
    from api.settings import store

    host = await store.get(db, "smtp.host") or settings.smtp_host
    sender = await store.get(db, "smtp.from") or settings.smtp_from
    password = await store.get(db, "smtp.password") or settings.smtp_password
    return MailConfig(
        host=host,
        port=await store.get(db, "smtp.port") or settings.smtp_port,
        username=await store.get(db, "smtp.username") or settings.smtp_username,
        password=password,
        sender=sender,
        use_tls=await store.get(db, "smtp.use_tls"),
        use_ssl=await store.get(db, "smtp.use_ssl"),
    )


def send(config: MailConfig, *, to: str, subject: str, body: str) -> bool:
    """Send one message. Returns whether it went.

    Failures are logged and reported, never raised into the request: a mail server that
    is down must not turn a password reset into a 500, and the caller has a designed
    answer for "it did not go" already.

    **Runs on the event loop's thread.** `smtplib` is blocking, so the caller hands this
    to a worker thread — see `api/routes/auth.py`. A slow mail server would otherwise
    stall every other request on the process, which is the one thing `agent/` and
    `api/` are split apart to prevent.
    """
    message = EmailMessage()
    message["From"] = config.sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        if config.use_ssl:
            server: smtplib.SMTP = smtplib.SMTP_SSL(config.host, config.port, timeout=10)
        else:
            server = smtplib.SMTP(config.host, config.port, timeout=10)
            if config.use_tls:
                server.starttls()

        with server:
            if config.username:
                server.login(config.username, config.password or "")
            server.send_message(message)
    except Exception:
        # The address is not logged: it is personal data, and a log line naming who was
        # sent a reset code is a log line worth stealing.
        logger.exception("could not send mail", extra={"subject": subject})
        return False

    logger.info("mail sent", extra={"subject": subject})
    return True


def can_connect(config: MailConfig, *, timeout: float = 5.0) -> bool:
    """Whether the mail server answers, without sending anything.

    The health screen asks this on a beat, so it must not put a message in anybody's
    inbox. It connects, negotiates TLS and signs in - which is where a wrong password
    actually shows up - then quits without composing mail.

    A shorter timeout than `send`: this runs while somebody is looking at a screen, and
    a mail server that takes ten seconds to answer has already told them what they
    needed to know.

    Blocking, like `send`. The caller hands it to a worker thread.
    """
    if not config.host:
        return False
    try:
        if config.use_ssl:
            server: smtplib.SMTP = smtplib.SMTP_SSL(config.host, config.port, timeout=timeout)
        else:
            server = smtplib.SMTP(config.host, config.port, timeout=timeout)
            if config.use_tls:
                server.starttls()
        with server:
            if config.username:
                server.login(config.username, config.password or "")
            server.noop()
    except Exception as error:
        # Logged at warning, not exception: an unreachable mail server on a screen
        # somebody is refreshing would otherwise write a traceback every few seconds.
        logger.warning(
            "mail server did not answer",
            extra={"host": config.host, "port": config.port, "reason": str(error)[:200]},
        )
        return False
    return True


def reset_code_body(code: str, minutes: int) -> str:
    return (
        f"Your Tel-Agent sign-in code is {code}.\n\n"
        f"It is good for {minutes} minutes and can be used once. "
        "If you did not ask for it, you can ignore this message - "
        "nothing has changed on your account."
    )
