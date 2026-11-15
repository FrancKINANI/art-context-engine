"""Outbound mail: one message type, the sign-in link.

Sign-in is passwordless, so the link *is* the credential. That makes this file a
security boundary, not a convenience:

* the token is random (32 bytes), single-use and short-lived;
* only its SHA-256 is stored, so a database leak does not hand out sessions;
* the message body contains the link and nothing else, so a forwarded email
  leaks no account data.

Without SMTP configuration the mailer refuses to send. Production boot fails
when billing is enabled and mail is missing (see ``config.production_problems``)
— a paid install whose sign-in cannot verify an address would let anyone claim
a subscriber's account by typing their email.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from src.config import settings


class MailerDisabled(RuntimeError):
    """Raised when a message is requested without SMTP configuration."""


def configured() -> bool:
    return settings.mail_configured


def send_sign_in_link(email: str, link: str) -> None:
    if not configured():
        raise MailerDisabled("ACE_SMTP_HOST is not configured")

    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = email
    message["Subject"] = "Your Art Context Engine sign-in link"
    message.set_content(
        "Open this link to sign in. It works once and expires in 15 minutes.\n\n"
        f"{link}\n\n"
        "If you did not ask for this, ignore the message — no account was created.\n"
    )

    context = ssl.create_default_context()
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        smtp.starttls(context=context)
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password or "")
        smtp.send_message(message)


def send_sign_in_link_safely(email: str, link: str) -> bool:
    """Best-effort send for the API path.

    A failed send must not tell the caller whether the address exists, and must
    not 500 the sign-in form: the token is already stored and the reader can ask
    for another. The failure is logged by the route.
    """
    try:
        send_sign_in_link(email, link)
        return True
    except (MailerDisabled, OSError, smtplib.SMTPException):
        return False
