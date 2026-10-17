import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException, Request

from app.core.config import get_settings

SESSION_COOKIE = "hrmind_session"
SESSION_DAYS = 7
VERIFICATION_HOURS = 24
RESET_MINUTES = 30
PBKDF2_ITERATIONS = 310_000

settings = get_settings()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@contextmanager
def auth_connection():
    path = Path(settings.auth_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(str(path), timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_auth_db() -> None:
    with auth_connection() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'employee'
                    CHECK (role IN ('employee', 'admin')),
                is_verified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS auth_tokens (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                purpose TEXT NOT NULL
                    CHECK (
                        purpose IN ('verify_email', 'password_reset')
                    ),
                expires_at TEXT NOT NULL,
                used_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY(user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_auth_tokens_user
            ON auth_tokens(user_id, purpose, created_at)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_user
            ON auth_sessions(user_id)
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS company_documents (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                vector_ids_json TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                uploaded_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(uploaded_by) REFERENCES users(id)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS personal_documents (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                namespace TEXT NOT NULL,
                vector_ids_json TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_personal_docs_user
            ON personal_documents(user_id, created_at)
        """)

        now = _iso()

        db.execute(
            "DELETE FROM auth_sessions WHERE expires_at <= ?",
            (now,),
        )

        db.execute(
            """
            DELETE FROM auth_tokens
            WHERE expires_at <= ? OR used_at IS NOT NULL
        """,
            (now,),
        )


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )

    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}" f"${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_hex, expected_hex = encoded.split("$", 3)

        if algorithm != "pbkdf2_sha256":
            return False

        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations_text),
        )

        return hmac.compare_digest(
            digest.hex(),
            expected_hex,
        )

    except (ValueError, TypeError):
        return False


def public_user(row: sqlite3.Row | dict) -> dict:
    return {
        "id": row["id"],
        "display_name": row["display_name"],
        "email": row["email"],
        "role": row["role"],
        "is_verified": bool(row["is_verified"]),
        "created_at": row["created_at"],
    }


def create_user(
    display_name: str,
    email: str,
    password: str,
) -> tuple[dict, str]:
    user_id = secrets.token_hex(16)
    now = _iso()
    email = normalize_email(email)
    name = " ".join(display_name.strip().split())

    if not name or len(name) > 80:
        raise ValueError("Name must be between 1 and 80 characters.")

    if len(password) < 12 or len(password) > 128:
        raise ValueError("Password must be between 12 and 128 characters.")

    if (
        "@" not in email
        or len(email) > 254
        or email.startswith("@")
        or email.endswith("@")
    ):
        raise ValueError("Enter a valid email address.")

    with auth_connection() as db:
        existing = db.execute(
            "SELECT id FROM users WHERE email = ?",
            (email,),
        ).fetchone()

        if existing:
            raise ValueError(
                "An account with this email already exists. " "Try signing in."
            )

        db.execute(
            """
            INSERT INTO users (
                id, display_name, email, password_hash,
                role, is_verified, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'employee', 0, ?, ?)
            """,
            (
                user_id,
                name,
                email,
                hash_password(password),
                now,
                now,
            ),
        )

        row = db.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

    token = issue_token(
        user_id,
        "verify_email",
        timedelta(hours=VERIFICATION_HOURS),
    )

    return public_user(row), token


def issue_token(
    user_id: str,
    purpose: str,
    lifetime: timedelta,
) -> str:
    raw = secrets.token_urlsafe(32)
    now = utc_now()

    with auth_connection() as db:
        # Only the latest outstanding token for each purpose is valid.
        db.execute(
            """
            UPDATE auth_tokens
            SET used_at = ?
            WHERE user_id = ?
                AND purpose = ?
                AND used_at IS NULL
            """,
            (_iso(now), user_id, purpose),
        )

        db.execute(
            """
            INSERT INTO auth_tokens (
                token_hash, user_id, purpose,
                expires_at, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                _token_hash(raw),
                user_id,
                purpose,
                _iso(now + lifetime),
                _iso(now),
            ),
        )

    return raw


def verify_email_token(raw_token: str) -> dict | None:
    now = _iso()

    with auth_connection() as db:
        row = db.execute(
            """
            SELECT t.token_hash, t.user_id, u.email
            FROM auth_tokens AS t
            JOIN users AS u ON u.id = t.user_id
            WHERE t.token_hash = ?
                AND t.purpose = 'verify_email'
                AND t.used_at IS NULL
                AND t.expires_at > ?
            """,
            (_token_hash(raw_token), now),
        ).fetchone()

        if not row:
            return None

        configured_admin_email = (
            normalize_email(settings.admin_email) if settings.admin_email else ""
        )

        role = (
            "admin"
            if configured_admin_email and row["email"] == configured_admin_email
            else "employee"
        )

        db.execute(
            """
            UPDATE auth_tokens
            SET used_at = ?
            WHERE token_hash = ?
            """,
            (now, row["token_hash"]),
        )

        db.execute(
            """
            UPDATE users
            SET is_verified = 1, role = ?, updated_at = ?
            WHERE id = ?
            """,
            (role, now, row["user_id"]),
        )

        user = db.execute(
            "SELECT * FROM users WHERE id = ?",
            (row["user_id"],),
        ).fetchone()

    return public_user(user)


def get_user_for_login(email: str):
    with auth_connection() as db:
        return db.execute(
            "SELECT * FROM users WHERE email = ?",
            (normalize_email(email),),
        ).fetchone()


def create_session(user_id: str) -> str:
    raw = secrets.token_urlsafe(32)
    now = utc_now()

    with auth_connection() as db:
        db.execute(
            """
            INSERT INTO auth_sessions (
                token_hash, user_id, created_at, expires_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                _token_hash(raw),
                user_id,
                _iso(now),
                _iso(now + timedelta(days=SESSION_DAYS)),
            ),
        )

    return raw


def user_for_session(raw_session: str | None) -> dict | None:
    if not raw_session:
        return None

    now = _iso()

    with auth_connection() as db:
        row = db.execute(
            """
            SELECT u.*
            FROM auth_sessions AS s
            JOIN users AS u ON u.id = s.user_id
            WHERE s.token_hash = ?
                AND s.expires_at > ?
                AND u.is_verified = 1
            """,
            (_token_hash(raw_session), now),
        ).fetchone()

    return public_user(row) if row else None


def revoke_session(raw_session: str | None) -> None:
    if not raw_session:
        return

    with auth_connection() as db:
        db.execute(
            "DELETE FROM auth_sessions WHERE token_hash = ?",
            (_token_hash(raw_session),),
        )


def request_reset_token(
    email: str,
) -> tuple[dict | None, str | None]:
    user = get_user_for_login(email)

    if not user or not user["is_verified"]:
        return None, None

    token = issue_token(
        user["id"],
        "password_reset",
        timedelta(minutes=RESET_MINUTES),
    )

    return public_user(user), token


def reset_password(
    raw_token: str,
    password: str,
) -> bool:
    if len(password) < 12 or len(password) > 128:
        raise ValueError("Password must be between 12 and 128 characters.")

    now = _iso()

    with auth_connection() as db:
        row = db.execute(
            """
            SELECT token_hash, user_id
            FROM auth_tokens
            WHERE token_hash = ?
                AND purpose = 'password_reset'
                AND used_at IS NULL
                AND expires_at > ?
            """,
            (_token_hash(raw_token), now),
        ).fetchone()

        if not row:
            return False

        db.execute(
            "UPDATE auth_tokens SET used_at = ? WHERE token_hash = ?",
            (now, row["token_hash"]),
        )

        db.execute(
            """
            UPDATE users
            SET password_hash = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                hash_password(password),
                now,
                row["user_id"],
            ),
        )

        # Password reset invalidates every existing session for this user.
        db.execute(
            "DELETE FROM auth_sessions WHERE user_id = ?",
            (row["user_id"],),
        )

    return True


def get_current_user(request: Request) -> dict:
    from app.services.authentication import user_for_session

    user = user_for_session(request.cookies.get(SESSION_COOKIE))

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Please sign in to continue.",
        )

    return user


def require_admin(user: dict) -> None:
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Administrator access is required.",
        )


def create_company_document(
    doc_id: str,
    filename: str,
    storage_path: str,
    vector_ids: list[str],
    size_bytes: int,
    uploaded_by: str,
) -> None:
    with auth_connection() as db:
        db.execute(
            """
            INSERT INTO company_documents (
                id, filename, storage_path,
                vector_ids_json, size_bytes,
                uploaded_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                filename,
                storage_path,
                json.dumps(vector_ids),
                size_bytes,
                uploaded_by,
                _iso(),
            ),
        )


def list_company_documents() -> list[dict]:
    with auth_connection() as db:
        rows = db.execute("""
            SELECT id, filename, size_bytes, uploaded_by, created_at
            FROM company_documents
            ORDER BY created_at DESC
            """).fetchall()

    return [dict(row) for row in rows]


def get_company_document(doc_id: str) -> dict | None:
    with auth_connection() as db:
        row = db.execute(
            "SELECT * FROM company_documents WHERE id = ?",
            (doc_id,),
        ).fetchone()

    if not row:
        return None

    result = dict(row)
    result["vector_ids"] = json.loads(result.pop("vector_ids_json"))

    return result


def delete_company_document_record(doc_id: str) -> None:
    with auth_connection() as db:
        db.execute(
            "DELETE FROM company_documents WHERE id = ?",
            (doc_id,),
        )


def create_personal_document(
    doc_id: str,
    user_id: str,
    filename: str,
    storage_path: str,
    namespace: str,
    vector_ids: list[str],
    size_bytes: int,
) -> None:
    with auth_connection() as db:
        db.execute(
            """
            INSERT INTO personal_documents (
                id, user_id, filename, storage_path,
                namespace, vector_ids_json, size_bytes, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                user_id,
                filename,
                storage_path,
                namespace,
                json.dumps(vector_ids),
                size_bytes,
                _iso(),
            ),
        )


def list_personal_documents(user_id: str) -> list[dict]:
    with auth_connection() as db:
        rows = db.execute(
            """
            SELECT id, filename, size_bytes, created_at
            FROM personal_documents
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def get_personal_document(
    doc_id: str,
    user_id: str,
) -> dict | None:
    with auth_connection() as db:
        row = db.execute(
            """
            SELECT *
            FROM personal_documents
            WHERE id = ? AND user_id = ?
            """,
            (doc_id, user_id),
        ).fetchone()

    if not row:
        return None

    result = dict(row)
    result["vector_ids"] = json.loads(result.pop("vector_ids_json"))

    return result


def delete_personal_document_record(
    doc_id: str,
    user_id: str,
) -> None:
    with auth_connection() as db:
        db.execute(
            """
            DELETE FROM personal_documents
            WHERE id = ? AND user_id = ?
            """,
            (doc_id, user_id),
        )


def claim_legacy_conversations(
    legacy_owner_id: str | None,
    user_id: str,
) -> None:
    """Associate this browser's old chats with its first signed-in account."""
    if not legacy_owner_id:
        return

    path = Path(settings.conversation_db_path)

    if not path.exists():
        return

    connection = sqlite3.connect(str(path), timeout=5)

    try:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(conversations)").fetchall()
        }

        if "owner_id" not in columns:
            return

        connection.execute(
            """
            UPDATE conversations
            SET owner_id = ?
            WHERE owner_id = ?
            """,
            (user_id, legacy_owner_id),
        )

        connection.commit()

    finally:
        connection.close()
