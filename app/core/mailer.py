"""Sending email. Backends: console (development), memory (tests), smtp, resend (HTTP API)."""
import logging
import smtplib
import ssl
from email.message import EmailMessage

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)
OUTBOX: list[dict] = []  # filled by the "memory" backend and read by tests
RESEND_URL = "https://api.resend.com/emails"


def _http() -> httpx.Client:
    return httpx.Client(timeout=10.0)  # tests replace this with a fake


def _send_smtp(to: str, subject: str, body: str) -> None:
    s = get_settings()
    if not s.smtp_host:
        raise RuntimeError("SMTP_HOST is not set")
    message = EmailMessage()
    message["From"] = s.email_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    def deliver(server) -> None:
        if s.smtp_user:
            server.login(s.smtp_user, s.smtp_password)
        server.send_message(message)

    context = ssl.create_default_context()
    if s.smtp_port == 465:
        with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=10, context=context) as server:
            deliver(server)
    else:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as server:
            if s.smtp_starttls:
                server.starttls(context=context)
            deliver(server)


def _send_resend(to: str, subject: str, body: str) -> None:
    s = get_settings()
    if not s.resend_api_key:
        raise RuntimeError("RESEND_API_KEY is not set")
    with _http() as http:
        response = http.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {s.resend_api_key}"},
            json={"from": s.email_from, "to": [to], "subject": subject, "text": body},
        )
    response.raise_for_status()


def send_email(to: str, subject: str, body: str) -> None:
    """Never raises: a failed email must not break the request that triggered it."""
    s = get_settings()
    try:
        if s.email_backend == "memory":
            OUTBOX.append({"to": to, "subject": subject, "body": body})
        elif s.email_backend == "smtp":
            _send_smtp(to, subject, body)
        elif s.email_backend == "resend":
            _send_resend(to, subject, body)
        elif s.env == "development":
            print(f"\n--- EMAIL (console) ---\nTo: {to}\nSubject: {subject}\n\n{body}\n-----------------------\n", flush=True)
        else:
            # The console backend would write secret links into the logs, so it is dev-only.
            log.warning("Email backend is 'console' outside development; message suppressed (%s)", subject)
    except Exception:
        log.exception("Could not send email (%s)", subject)