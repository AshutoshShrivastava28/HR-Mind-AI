import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import get_settings


def smtp_is_configured() -> bool:
    settings = get_settings()

    return bool(settings.smtp_host and settings.smtp_from_email)


def send_email(
    to_email: str,
    subject: str,
    body: str,
) -> None:
    settings = get_settings()

    if not smtp_is_configured():
        raise RuntimeError("SMTP email delivery is not configured.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from_email
    message["To"] = to_email
    message.set_content(body)

    context = ssl.create_default_context()

    with smtplib.SMTP(
        settings.smtp_host,
        settings.smtp_port,
        timeout=15,
    ) as server:
        if settings.smtp_use_tls:
            server.starttls(context=context)

        if settings.smtp_username:
            server.login(
                settings.smtp_username,
                settings.smtp_password,
            )

        server.send_message(message)
