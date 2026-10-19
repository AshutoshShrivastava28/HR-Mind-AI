import json
import logging
import re
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.services.authentication import (
    SESSION_COOKIE,
    SESSION_DAYS,
    auth_connection,
    claim_legacy_conversations,
    create_session,
    create_user,
    get_current_user,
    get_user_for_login,
    issue_token,
    normalize_email,
    public_user,
    request_reset_token,
    require_admin,
    reset_password,
    revoke_session,
    verify_email_token,
    verify_password,
)
from app.services.email_sender import send_email, smtp_is_configured
from app.services.rate_limiter import enforce_rate_limit

router = APIRouter(prefix="/api/auth", tags=["authentication"])
settings = get_settings()
logger = logging.getLogger(__name__)


class RegisterRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class TokenRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class EmailRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=12, max_length=128)


class UserRoleUpdateRequest(BaseModel):
    role: str = Field(min_length=1, max_length=20)


def _is_local_request(request: Request) -> bool:
    return (request.url.hostname or "").lower() in {
        "localhost",
        "127.0.0.1",
        "::1",
    }


def _require_secure_transport(request: Request) -> None:
    if not _is_local_request(request) and request.url.scheme != "https":
        raise HTTPException(
            status_code=426,
            detail=(
                "Authentication requires HTTPS. Configure a domain "
                "and TLS certificate before enabling public sign-in."
            ),
        )


def _base_url(request: Request) -> str:
    configured = settings.public_base_url.strip().rstrip("/")
    return configured or str(request.base_url).rstrip("/")


def _token_url(request: Request, parameter: str, token: str) -> str:
    return f"{_base_url(request)}/?" f"{urlencode({parameter: token})}"


def _deliver_or_dev_link(
    request: Request,
    email: str,
    subject: str,
    body: str,
    parameter: str,
    token: str,
) -> str | None:
    url = _token_url(request, parameter, token)

    if smtp_is_configured():
        try:
            send_email(
                email,
                subject,
                body.replace("{{LINK}}", url),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="Email could not be sent. Check email-delivery configuration and application logs.",
            ) from exc

        return None

    if _is_local_request(request) and settings.app_env.lower() != "production":
        return url

    raise HTTPException(
        status_code=503,
        detail=(
            "Email delivery is not configured. "
            "Configure Gmail API or SMTP before enabling account verification."
        ),
    )


# ---------------------------------------------------------
# Registration and email verification
# ---------------------------------------------------------


@router.post("/register", status_code=201)
def register(payload: RegisterRequest, request: Request):
    _require_secure_transport(request)
    enforce_rate_limit(request, "register-ip", limit=10, window_seconds=3600)

    email = normalize_email(payload.email)

    enforce_rate_limit(
        request,
        "register-email",
        limit=3,
        window_seconds=3600,
        identity=email,
        identity_only=True,
    )

    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(
            status_code=400,
            detail="Enter a valid email address.",
        )

    local_development = (
        _is_local_request(request) and settings.app_env.lower() != "production"
    )

    if not smtp_is_configured() and not local_development:
        raise HTTPException(
            status_code=503,
            detail=(
                "Email delivery must be configured before "
                "registration can be enabled."
            ),
        )

    try:
        _user, token = create_user(
            payload.display_name,
            email,
            payload.password,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from None

    verification_url = _deliver_or_dev_link(
        request,
        email,
        "Verify your HR Mind AI account",
        (
            "Welcome to HR Mind AI. Verify your email address "
            "using this link:\n\n{{LINK}}\n\n"
            "This link expires in 24 hours. "
            "If you did not create this account, ignore this email."
        ),
        "verify",
        token,
    )

    result = {"message": "Account created. Verify your email before signing in."}

    if verification_url:
        result["development_verification_url"] = verification_url

    return result


@router.post("/verify-email")
def verify_email(payload: TokenRequest, request: Request):
    _require_secure_transport(request)

    enforce_rate_limit(request, "verify-email-ip", limit=20, window_seconds=900)

    user = verify_email_token(payload.token)

    if not user:
        raise HTTPException(
            status_code=400,
            detail=(
                "This verification link is invalid or has expired. "
                "Request a new one."
            ),
        )

    return {
        "message": "Email verified. You can now sign in.",
        "user": user,
    }


@router.post("/resend-verification")
def resend_verification(payload: EmailRequest, request: Request):
    _require_secure_transport(request)

    enforce_rate_limit(request, "resend-verification-ip", limit=15, window_seconds=3600)

    email = normalize_email(payload.email)

    enforce_rate_limit(
        request,
        "resend-verification-email",
        limit=5,
        window_seconds=3600,
        identity=email,
        identity_only=True,
    )

    local_development = (
        _is_local_request(request) and settings.app_env.lower() != "production"
    )

    if not smtp_is_configured() and not local_development:
        raise HTTPException(
            status_code=503,
            detail="Email delivery is not configured.",
        )

    row = get_user_for_login(email)

    result = {
        "message": (
            "If an unverified account exists for that address, "
            "a verification email has been sent."
        )
    }

    if not row or row["is_verified"]:
        return result

    token = issue_token(
        row["id"],
        "verify_email",
        timedelta(hours=24),
    )

    url = _deliver_or_dev_link(
        request,
        email,
        "Verify your HR Mind AI account",
        (
            "Use this link to verify your account:\n\n{{LINK}}\n\n"
            "This link expires in 24 hours."
        ),
        "verify",
        token,
    )

    if url:
        result["development_verification_url"] = url

    return result


# ---------------------------------------------------------
# Login and sessions
# ---------------------------------------------------------


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
):
    _require_secure_transport(request)

    enforce_rate_limit(request, "login-ip", limit=30, window_seconds=900)

    # This account-specific attempt limit is also scoped to the caller's IP.
    enforce_rate_limit(
        request,
        "login-email",
        limit=8,
        window_seconds=900,
        identity=normalize_email(payload.email),
    )

    row = get_user_for_login(payload.email)

    if not row or not verify_password(
        payload.password,
        row["password_hash"],
    ):
        raise HTTPException(
            status_code=401,
            detail="Email or password is incorrect.",
        )

    if not row["is_verified"]:
        raise HTTPException(
            status_code=403,
            detail="Verify your email address before signing in.",
        )

    token = create_session(row["id"])

    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=SESSION_DAYS * 24 * 60 * 60,
        path="/",
    )

    claim_legacy_conversations(
        request.cookies.get("hrmind_owner"),
        row["id"],
    )

    return {
        "user": public_user(row),
        "message": "Signed in successfully.",
    }


@router.post("/logout")
def logout(request: Request, response: Response):
    _require_secure_transport(request)
    revoke_session(request.cookies.get(SESSION_COOKIE))

    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )

    return {"message": "Signed out."}


@router.get("/me")
def me(request: Request):
    _require_secure_transport(request)
    return {"user": get_current_user(request)}


# ---------------------------------------------------------
# Password reset
# ---------------------------------------------------------


@router.post("/request-password-reset")
def request_password_reset(payload: EmailRequest, request: Request):
    _require_secure_transport(request)

    enforce_rate_limit(request, "password-reset-ip", limit=15, window_seconds=1800)

    enforce_rate_limit(
        request,
        "password-reset-email",
        limit=5,
        window_seconds=1800,
        identity=normalize_email(payload.email),
        identity_only=True,
    )

    local_development = (
        _is_local_request(request) and settings.app_env.lower() != "production"
    )

    if not smtp_is_configured() and not local_development:
        raise HTTPException(
            status_code=503,
            detail="Email delivery is not configured.",
        )

    user, token = request_reset_token(payload.email)

    result = {
        "message": (
            "If a verified account exists for that address, "
            "password-reset instructions have been sent."
        )
    }

    if not user or not token:
        return result

    url = _deliver_or_dev_link(
        request,
        user["email"],
        "Reset your HR Mind AI password",
        (
            "Use this link to set a new password:\n\n{{LINK}}\n\n"
            "This link expires in 30 minutes. "
            "If you did not request this, ignore this email."
        ),
        "reset",
        token,
    )

    if url:
        result["development_reset_url"] = url

    return result


@router.post("/reset-password")
def reset_password_endpoint(
    payload: ResetPasswordRequest,
    request: Request,
):
    _require_secure_transport(request)

    enforce_rate_limit(request, "reset-password-ip", limit=20, window_seconds=900)

    try:
        succeeded = reset_password(
            payload.token,
            payload.password,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from None

    if not succeeded:
        raise HTTPException(
            status_code=400,
            detail=("This reset link is invalid or has expired. Request another one."),
        )

    return {"message": "Password updated. Sign in with your new password."}


# ---------------------------------------------------------
# Administrator user management
# ---------------------------------------------------------


@router.get("/admin/users")
def admin_list_users(request: Request):
    current_user = get_current_user(request)
    require_admin(current_user)

    with auth_connection() as db:
        rows = db.execute("""
            SELECT id, display_name, email, role, is_verified, created_at
            FROM users
            ORDER BY created_at DESC
            """).fetchall()

    return {
        "users": [
            {
                **dict(row),
                "is_verified": bool(row["is_verified"]),
            }
            for row in rows
        ]
    }


@router.patch("/admin/users/{user_id}/role")
def admin_update_user_role(
    user_id: str,
    payload: UserRoleUpdateRequest,
    request: Request,
):
    current_user = get_current_user(request)
    require_admin(current_user)

    enforce_rate_limit(request, "admin-role-change", limit=60, window_seconds=3600)

    new_role = payload.role.strip().lower()

    if new_role not in {"admin", "employee"}:
        raise HTTPException(
            status_code=400,
            detail="Role must be admin or employee.",
        )

    if current_user["id"] == user_id and new_role != "admin":
        raise HTTPException(
            status_code=400,
            detail="You cannot remove your own administrator role.",
        )

    with auth_connection() as db:
        target = db.execute(
            """
            SELECT id, email, role, is_verified
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

        if not target:
            raise HTTPException(status_code=404, detail="User not found.")

        if new_role == "admin" and not target["is_verified"]:
            raise HTTPException(
                status_code=400,
                detail="Verify this user's email before granting administrator access.",
            )

        if target["role"] == "admin" and new_role != "admin":
            count = db.execute("""
                SELECT COUNT(*) AS total
                FROM users
                WHERE role = 'admin' AND is_verified = 1
                """).fetchone()["total"]

            if count <= 1:
                raise HTTPException(
                    status_code=400,
                    detail="You cannot demote the last verified administrator.",
                )

        db.execute(
            """
            UPDATE users
            SET role = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                new_role,
                datetime.now(timezone.utc).isoformat(),
                user_id,
            ),
        )

    return {
        "updated": True,
        "user_id": user_id,
        "role": new_role,
    }


@router.delete("/admin/users/{user_id}")
def admin_delete_user(user_id: str, request: Request):
    """Delete an account and private data. Administrators only."""
    from app.rag.vectorstore import delete_vectors

    current_user = get_current_user(request)
    require_admin(current_user)

    enforce_rate_limit(request, "admin-delete-user", limit=30, window_seconds=3600)

    if user_id == current_user["id"]:
        raise HTTPException(
            status_code=400,
            detail="You cannot delete your own account.",
        )

    with auth_connection() as db:
        target = db.execute(
            """
            SELECT id, role, is_verified
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

        if not target:
            raise HTTPException(status_code=404, detail="User not found.")

        if target["role"] == "admin" and target["is_verified"]:
            count = db.execute("""
                SELECT COUNT(*)
                FROM users
                WHERE role = 'admin' AND is_verified = 1
                """).fetchone()[0]

            if count <= 1:
                raise HTTPException(
                    status_code=409,
                    detail="The last verified administrator cannot be deleted.",
                )

        personal_docs = db.execute(
            """
            SELECT storage_path, namespace, vector_ids_json
            FROM personal_documents
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchall()

    # Remove private vectors first. Failure stops deletion and allows retry.
    try:
        for document in personal_docs:
            vector_ids = json.loads(document["vector_ids_json"] or "[]")
            if vector_ids:
                delete_vectors(
                    vector_ids,
                    namespace=document["namespace"],
                )
    except Exception:
        logger.exception("Private vector deletion failed for user %s", user_id)
        raise HTTPException(
            status_code=503,
            detail="Private vectors could not be removed. Check server logs and retry.",
        ) from None

    # Delete only the target user's private upload folder.
    upload_root = Path(settings.upload_dir).resolve()
    users_root = (upload_root / "users").resolve()
    user_upload_dir = (users_root / user_id).resolve()

    if user_upload_dir == users_root or users_root not in user_upload_dir.parents:
        raise HTTPException(
            status_code=400,
            detail="The user's upload directory is invalid.",
        )

    try:
        if user_upload_dir.exists():
            shutil.rmtree(user_upload_dir)
    except Exception:
        logger.exception("Could not clean upload directory for user %s", user_id)
        raise HTTPException(
            status_code=503,
            detail=(
                "Private files could not be removed. Account deletion has not "
                "completed; check the logs and retry."
            ),
        ) from None

    # Remove conversations in a separately managed SQLite database.
    conversation_db = Path(settings.conversation_db_path)

    try:
        if conversation_db.exists():
            with closing(sqlite3.connect(str(conversation_db), timeout=15)) as con:
                with con:
                    con.execute("PRAGMA foreign_keys = ON")

                    tables = {
                        row[0]
                        for row in con.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'"
                        ).fetchall()
                    }

                    if {
                        "conversations",
                        "conversation_messages",
                    }.issubset(tables):
                        con.execute(
                            """
                            DELETE FROM conversation_messages
                            WHERE conversation_id IN (
                                SELECT id
                                FROM conversations
                                WHERE owner_id = ?
                            )
                            """,
                            (user_id,),
                        )

                        con.execute(
                            "DELETE FROM conversations WHERE owner_id = ?",
                            (user_id,),
                        )
    except Exception:
        logger.exception("Conversation deletion failed for user %s", user_id)
        raise HTTPException(
            status_code=503,
            detail="Conversation history could not be removed. Check server logs and retry.",
        ) from None

    # Retain shared company documents and reassign their uploader attribution.
    try:
        with auth_connection() as db:
            target = db.execute(
                """
                SELECT role, is_verified
                FROM users
                WHERE id = ?
                """,
                (user_id,),
            ).fetchone()

            if not target:
                raise HTTPException(status_code=404, detail="User not found.")

            if target["role"] == "admin" and target["is_verified"]:
                count = db.execute("""
                    SELECT COUNT(*)
                    FROM users
                    WHERE role = 'admin' AND is_verified = 1
                    """).fetchone()[0]

                if count <= 1:
                    raise HTTPException(
                        status_code=409,
                        detail="The last verified administrator cannot be deleted.",
                    )

            db.execute(
                "UPDATE company_documents SET uploaded_by = ? WHERE uploaded_by = ?",
                (current_user["id"], user_id),
            )
            db.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
            db.execute("DELETE FROM auth_tokens WHERE user_id = ?", (user_id,))
            db.execute("DELETE FROM personal_documents WHERE user_id = ?", (user_id,))
            db.execute("DELETE FROM users WHERE id = ?", (user_id,))

    except HTTPException:
        raise
    except Exception:
        logger.exception("Account deletion failed for user %s", user_id)
        raise HTTPException(
            status_code=503,
            detail="The account could not be fully removed. Check server logs.",
        ) from None

    logger.info(
        "Administrator %s deleted user %s",
        current_user["id"],
        user_id,
    )

    return {
        "deleted": True,
        "user_id": user_id,
        "message": "User account deleted successfully.",
    }
