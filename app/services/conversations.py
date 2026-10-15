import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings

settings = get_settings()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connection():
    db_path = Path(settings.conversation_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(str(db_path), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")

    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_conversation_db() -> None:
    """Create the conversation and message tables if needed."""

    with _connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT 'New chat',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS conversation_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL
                    CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                source_used TEXT NOT NULL DEFAULT '',
                citations_json TEXT NOT NULL DEFAULT '[]',
                trace_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id)
                    REFERENCES conversations(id)
                    ON DELETE CASCADE
            )
        """)

        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversations_owner_updated
            ON conversations(owner_id, updated_at)
        """)

        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_conversation
            ON conversation_messages(conversation_id, id)
        """)


def create_conversation(owner_id: str) -> dict:
    """Create an empty conversation for one browser owner."""

    conversation_id = uuid.uuid4().hex
    timestamp = _now()

    with _connection() as con:
        con.execute(
            """
            INSERT INTO conversations (
                id, owner_id, title, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                owner_id,
                "New chat",
                timestamp,
                timestamp,
            ),
        )

    return {
        "id": conversation_id,
        "owner_id": owner_id,
        "title": "New chat",
        "created_at": timestamp,
        "updated_at": timestamp,
        "message_count": 0,
    }


def list_conversations(owner_id: str) -> list[dict]:
    """List only conversations belonging to the supplied browser owner."""

    with _connection() as con:
        rows = con.execute(
            """
            SELECT
                c.id,
                c.title,
                c.created_at,
                c.updated_at,
                COUNT(m.id) AS message_count
            FROM conversations AS c
            LEFT JOIN conversation_messages AS m
                ON m.conversation_id = c.id
            WHERE c.owner_id = ?
            GROUP BY c.id
            ORDER BY c.updated_at DESC
            """,
            (owner_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def get_conversation(
    owner_id: str,
    conversation_id: str,
) -> dict | None:
    """Get conversation details only when the owner matches."""

    with _connection() as con:
        row = con.execute(
            """
            SELECT
                c.id,
                c.title,
                c.created_at,
                c.updated_at,
                COUNT(m.id) AS message_count
            FROM conversations AS c
            LEFT JOIN conversation_messages AS m
                ON m.conversation_id = c.id
            WHERE c.id = ? AND c.owner_id = ?
            GROUP BY c.id
            """,
            (conversation_id, owner_id),
        ).fetchone()

    return dict(row) if row else None


def get_messages(
    owner_id: str,
    conversation_id: str,
) -> list[dict]:
    """Return saved messages in chronological order."""

    with _connection() as con:
        rows = con.execute(
            """
            SELECT
                m.id,
                m.role,
                m.content,
                m.source_used,
                m.citations_json,
                m.trace_json,
                m.created_at
            FROM conversation_messages AS m
            JOIN conversations AS c
                ON c.id = m.conversation_id
            WHERE c.id = ? AND c.owner_id = ?
            ORDER BY m.id ASC
            """,
            (conversation_id, owner_id),
        ).fetchall()

    messages = []

    for row in rows:
        item = dict(row)

        try:
            item["citations"] = json.loads(item.pop("citations_json"))
        except (TypeError, json.JSONDecodeError):
            item["citations"] = []

        try:
            item["trace"] = json.loads(item.pop("trace_json"))
        except (TypeError, json.JSONDecodeError):
            item["trace"] = []

        messages.append(item)

    return messages


def save_message(
    owner_id: str,
    conversation_id: str,
    role: str,
    content: str,
    source_used: str = "",
    citations: list | None = None,
    trace: list | None = None,
) -> int:
    """Save a user or assistant message to an owned conversation."""

    if role not in ("user", "assistant"):
        raise ValueError("Unsupported conversation message role.")

    timestamp = _now()

    with _connection() as con:
        conversation = con.execute(
            """
            SELECT
                title,
                (
                    SELECT COUNT(*)
                    FROM conversation_messages
                    WHERE conversation_id = conversations.id
                ) AS message_count
            FROM conversations
            WHERE id = ? AND owner_id = ?
            """,
            (conversation_id, owner_id),
        ).fetchone()

        if conversation is None:
            raise ValueError("Conversation not found.")

        con.execute(
            """
            INSERT INTO conversation_messages (
                conversation_id,
                role,
                content,
                source_used,
                citations_json,
                trace_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                role,
                content,
                source_used,
                json.dumps(citations or [], ensure_ascii=False),
                json.dumps(trace or [], ensure_ascii=False),
                timestamp,
            ),
        )

        title = conversation["title"]

        # Automatically title a new conversation from its first question.
        if role == "user" and conversation["message_count"] == 0:
            title = re.sub(r"\s+", " ", content).strip()

            if len(title) > 55:
                title = title[:52].rstrip() + "..."

            title = title or "New chat"

        con.execute(
            """
            UPDATE conversations
            SET title = ?, updated_at = ?
            WHERE id = ? AND owner_id = ?
            """,
            (title, timestamp, conversation_id, owner_id),
        )

        row = con.execute("SELECT last_insert_rowid() AS id").fetchone()

    return row["id"]


def rename_conversation(
    owner_id: str,
    conversation_id: str,
    title: str,
) -> bool:
    """Rename a conversation belonging to the specified owner."""

    title = re.sub(r"\s+", " ", title).strip()

    if not title or len(title) > 80:
        raise ValueError("Title must be between 1 and 80 characters.")

    with _connection() as con:
        cursor = con.execute(
            """
            UPDATE conversations
            SET title = ?, updated_at = ?
            WHERE id = ? AND owner_id = ?
            """,
            (title, _now(), conversation_id, owner_id),
        )

    return cursor.rowcount > 0


def delete_conversation(
    owner_id: str,
    conversation_id: str,
) -> bool:
    """Delete an owned conversation and its messages."""

    with _connection() as con:
        cursor = con.execute(
            """
            DELETE FROM conversations
            WHERE id = ? AND owner_id = ?
            """,
            (conversation_id, owner_id),
        )

    return cursor.rowcount > 0
