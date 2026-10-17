"""SQLite-backed rate limiting for security-sensitive endpoints."""

import hashlib
import math
import threading
import time

from fastapi import HTTPException, Request

from app.services.authentication import auth_connection

_schema_lock = threading.Lock()


def _ensure_schema() -> None:
    # Run this each time because tests and deployments may use different DBs.
    with _schema_lock:
        with auth_connection() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS auth_rate_limit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bucket TEXT NOT NULL,
                    subject_hash TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_rate_limit_subject_time
                ON auth_rate_limit_events(
                    bucket, subject_hash, created_at
                )
            """)


def enforce_rate_limit(
    request: Request,
    bucket: str,
    *,
    limit: int,
    window_seconds: int,
    identity: str | None = None,
    identity_only: bool = False,
) -> None:
    """Raise HTTP 429 when a client exceeds a sliding-window limit."""
    if limit < 1 or window_seconds < 1:
        raise ValueError("Rate-limit values must be positive.")

    _ensure_schema()

    client_ip = request.client.host if request.client else "unknown"
    normalized_identity = (identity or "").strip().lower()

    if identity_only and normalized_identity:
        subject = normalized_identity
    elif normalized_identity:
        subject = f"{client_ip}|{normalized_identity}"
    else:
        subject = client_ip

    subject_hash = hashlib.sha256(f"{bucket}|{subject}".encode("utf-8")).hexdigest()

    now = time.time()
    window_start = now - window_seconds

    with auth_connection() as db:
        db.execute("BEGIN IMMEDIATE")

        db.execute(
            "DELETE FROM auth_rate_limit_events WHERE created_at < ?",
            (now - 86400,),
        )

        events = db.execute(
            """
            SELECT created_at
            FROM auth_rate_limit_events
            WHERE bucket = ?
              AND subject_hash = ?
              AND created_at > ?
            ORDER BY created_at ASC
            """,
            (bucket, subject_hash, window_start),
        ).fetchall()

        if len(events) >= limit:
            retry_after = max(
                1,
                math.ceil(window_seconds - (now - events[0]["created_at"])),
            )

            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please wait before trying again.",
                headers={"Retry-After": str(retry_after)},
            )

        db.execute(
            """
            INSERT INTO auth_rate_limit_events (
                bucket, subject_hash, created_at
            )
            VALUES (?, ?, ?)
            """,
            (bucket, subject_hash, now),
        )
