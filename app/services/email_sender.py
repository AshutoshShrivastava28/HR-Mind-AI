import base64
import json
import logging
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

import requests

from app.core.config import get_settings

logger = logging.getLogger(__name__)

GMAIL_CREDENTIALS_FILE = Path("/app/data/gmail_oauth.json")
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


def _load_gmail_credentials():
    if not GMAIL_CREDENTIALS_FILE.is_file():
        return None

    try:
        data = json.loads(GMAIL_CREDENTIALS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("The Gmail OAuth credentials file could not be read.") from exc

    required = ("client_id", "client_secret", "refresh_token", "sender_email")
    missing = [
        key for key in required
        if not isinstance(data.get(key), str) or not data[key].strip()
    ]
    if missing:
        raise RuntimeError(
            "The Gmail OAuth credentials file is missing: " + ", ".join(missing)
        )

    return data


def smtp_is_configured() -> bool:
    # Keep the existing function name for compatibility with auth_routes.py.
    if GMAIL_CREDENTIALS_FILE.is_file():
        return True

    settings = get_settings()
    return bool(settings.smtp_host and settings.smtp_from_email)


def _send_via_gmail_api(
    to_email: str,
    subject: str,
    body: str,
    credentials: dict,
) -> None:
    try:
        token_response = requests.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": credentials["client_id"],
                "client_secret": credentials["client_secret"],
                "refresh_token": credentials["refresh_token"],
                "grant_type": "refresh_token",
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        logger.exception("Could not reach Google's OAuth endpoint over HTTPS")
        raise RuntimeError("Could not connect to Google's OAuth endpoint.") from exc

    if not token_response.ok:
        logger.error(
            "Google OAuth token refresh failed: HTTP %s; response=%s",
            token_response.status_code,
            token_response.text[:300],
        )
        raise RuntimeError(
            "Google OAuth token refresh failed. Check the OAuth credentials and refresh token."
        )

    try:
        token_data = token_response.json()
        access_token = token_data["access_token"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("Google did not return a usable access token.") from exc

    message = EmailMessage()
    message["To"] = to_email
    message["From"] = credentials["sender_email"]
    message["Subject"] = subject
    message.set_content(body)
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")

    try:
        response = requests.post(
            GMAIL_SEND_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"raw": raw_message},
            timeout=20,
        )
    except requests.RequestException as exc:
        logger.exception("Gmail API send request failed")
        raise RuntimeError("Could not connect to the Gmail API.") from exc

    if not response.ok:
        logger.error(
            "Gmail API send failed: HTTP %s; response=%s",
            response.status_code,
            response.text[:300],
        )
        raise RuntimeError(
            f"Gmail API rejected the message (HTTP {response.status_code}). "
            "Check the Gmail API, OAuth permission and sender account."
        )

    logger.info("Email sent through Gmail API to %s", to_email)


def send_email(to_email: str, subject: str, body: str) -> None:
    credentials = _load_gmail_credentials()
    if credentials is not None:
        _send_via_gmail_api(to_email, subject, body, credentials)
        return

    # Preserve SMTP as a fallback for local development or other deployments.
    settings = get_settings()
    if not (settings.smtp_host and settings.smtp_from_email):
        raise RuntimeError(
            "Email delivery is not configured. Configure Gmail API credentials or SMTP."
        )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from_email
    message["To"] = to_email
    message.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
        if settings.smtp_use_tls:
            server.starttls(context=context)
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(message)
