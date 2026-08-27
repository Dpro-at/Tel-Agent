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
from email.message import EmailMessage

from api.config import Settings

logger = logging.getLogger("api.mail")


def is_configured(settings: Settings) -> bool:
    """Can this installation send at all?"""
    return bool(settings.smtp_host and settings.smtp_from)


def send(settings: Settings, *, to: str, subject: str, body: str) -> bool:
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
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        if settings.smtp_use_ssl:
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, timeout=10
            )
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10)
            if settings.smtp_use_tls:
                server.starttls()

        with server:
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password or "")
            server.send_message(message)
    except Exception:
        # The address is not logged: it is personal data, and a log line naming who was
        # sent a reset code is a log line worth stealing.
        logger.exception("could not send mail", extra={"subject": subject})
        return False

    logger.info("mail sent", extra={"subject": subject})
    return True


def reset_code_body(code: str, minutes: int) -> str:
    return (
        f"Your Tel-Agent sign-in code is {code}.\n\n"
        f"It is good for {minutes} minutes and can be used once. "
        "If you did not ask for it, you can ignore this message - "
        "nothing has changed on your account."
    )
