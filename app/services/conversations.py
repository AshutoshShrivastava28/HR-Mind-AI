import hashlib
import json
import re
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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
        con.execute("""
            CREATE TABLE IF NOT EXISTS conversation_memories (
                conversation_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id)
                    REFERENCES conversations(id)
                    ON DELETE CASCADE
            )
        """)
        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversation_memories_owner_updated
            ON conversation_memories(owner_id, updated_at)
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS conversation_shares (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT,
                FOREIGN KEY (conversation_id)
                    REFERENCES conversations(id)
                    ON DELETE CASCADE
            )
        """)

        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversation_shares_owner
            ON conversation_shares(owner_id, conversation_id, created_at)
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


def get_memory_context(
    owner_id: str,
    conversation_id: str,
    question: str,
    recent_limit: int = 8,
    memory_limit: int = 5,
) -> str:
    """Build bounded, account-scoped context from this and prior conversations."""
    with _connection() as con:
        conversation = con.execute(
            "SELECT title FROM conversations WHERE id = ? AND owner_id = ?",
            (conversation_id, owner_id),
        ).fetchone()
        if conversation is None:
            raise ValueError("Conversation not found.")

        recent_rows = con.execute(
            """
            SELECT role, content FROM conversation_messages
            WHERE conversation_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (conversation_id, recent_limit),
        ).fetchall()
        memory_rows = con.execute(
            """
            SELECT m.conversation_id, c.title, m.summary, m.updated_at
            FROM conversation_memories AS m
            JOIN conversations AS c ON c.id = m.conversation_id
            WHERE m.owner_id = ? AND c.owner_id = ? AND m.summary <> ''
            ORDER BY m.updated_at DESC
            """,
            (owner_id, owner_id),
        ).fetchall()

    recent_rows = list(reversed(recent_rows))
    recent_text = "\n".join(
        f"{row['role'].title()}: {str(row['content'])[:1200]}"
        for row in recent_rows
    )

    # Rank prior conversation summaries by overlap with the new question, then
    # include a couple of recent summaries so ongoing work can carry forward.
    stop_words = {
        "about", "after", "also", "and", "are", "can", "could", "for",
        "from", "have", "how", "into", "more", "please", "that", "the",
        "then", "this", "what", "when", "where", "which", "who", "with",
        "would", "you", "your",
    }
    query_terms = {
        token.casefold().strip(".,!?;:()[]{}\"'")
        for token in question.split()
        if len(token) > 2 and token.casefold() not in stop_words
    }
    ranked = []
    for index, row in enumerate(memory_rows):
        summary_terms = set(
            token.casefold().strip(".,!?;:()[]{}\"'")
            for token in f"{row['title']} {row['summary']}".split()
            if len(token) > 2
        )
        overlap = len(query_terms & summary_terms)
        recency_bonus = max(0, 5 - index)
        ranked.append((overlap * 10 + recency_bonus, index, row))
    ranked.sort(key=lambda item: (item[0], -item[1]), reverse=True)

    selected = []
    seen = set()
    for _, _, row in ranked:
        if row["conversation_id"] == conversation_id or row["conversation_id"] in seen:
            continue
        if len(selected) >= memory_limit:
            break
        seen.add(row["conversation_id"])
        selected.append(row)

    sections = []
    if selected:
        prior = "\n".join(
            f"- {str(row['title'])[:100]}: {str(row['summary'])[:900]}"
            for row in selected
        )
        sections.append("Relevant memory from this user's other chats:\n" + prior)
    if recent_text:
        sections.append("Recent turns in this chat:\n" + recent_text)

    current_summary = next(
        (
            str(row["summary"])
            for row in memory_rows
            if row["conversation_id"] == conversation_id
        ),
        "",
    )
    if current_summary:
        sections.insert(0, "Earlier summary of this chat:\n" + current_summary[:1800])

    return "\n\n".join(sections)[:7000]


def get_conversation_memory(owner_id: str, conversation_id: str) -> str:
    with _connection() as con:
        row = con.execute(
            """
            SELECT m.summary
            FROM conversation_memories AS m
            JOIN conversations AS c ON c.id = m.conversation_id
            WHERE m.owner_id = ? AND m.conversation_id = ? AND c.owner_id = ?
            """,
            (owner_id, conversation_id, owner_id),
        ).fetchone()
    return str(row["summary"] or "") if row else ""


def get_recent_messages(
    owner_id: str,
    conversation_id: str,
    limit: int = 12,
) -> list[dict]:
    with _connection() as con:
        rows = con.execute(
            """
            SELECT m.role, m.content
            FROM conversation_messages AS m
            JOIN conversations AS c ON c.id = m.conversation_id
            WHERE c.id = ? AND c.owner_id = ?
            ORDER BY m.id DESC LIMIT ?
            """,
            (conversation_id, owner_id, limit),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def fallback_memory_summary(messages: list[dict]) -> str:
    """Create a safe, deterministic topic summary when model summarization is unavailable."""
    excluded_terms = (
        "password", "passcode", "social security", "ssn", "passport",
        "bank account", "credit card", "medical diagnosis", "health condition",
        "therapy", "medication", "medical history",
    )
    greetings = {
        "hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "bye",
    }
    topics = []
    for message in messages:
        if message.get("role") != "user":
            continue
        content = " ".join(str(message.get("content") or "").split())
        if len(content) < 10 or content.casefold().strip(".!?, ") in greetings:
            continue
        if any(term in content.casefold() for term in excluded_terms):
            continue
        topics.append(content[:220])
    if not topics:
        return ""
    return "Topics discussed: " + "; ".join(topics[-6:])


def update_conversation_memory(
    owner_id: str,
    conversation_id: str,
    summary: str,
) -> bool:
    """Save a rolling summary only if the conversation belongs to this user."""
    summary = " ".join(str(summary or "").split())[:4000]
    if not summary:
        with _connection() as con:
            cursor = con.execute(
                """
                DELETE FROM conversation_memories
                WHERE conversation_id = ? AND owner_id = ?
                """,
                (conversation_id, owner_id),
            )
            return cursor.rowcount > 0
    with _connection() as con:
        cursor = con.execute(
            """
            INSERT INTO conversation_memories
                (conversation_id, owner_id, summary, updated_at)
            SELECT id, owner_id, ?, ? FROM conversations
            WHERE id = ? AND owner_id = ?
            ON CONFLICT(conversation_id) DO UPDATE SET
                summary = excluded.summary,
                updated_at = excluded.updated_at
            """,
            (summary, _now(), conversation_id, owner_id),
        )
        return cursor.rowcount > 0


def list_conversation_memories(owner_id: str) -> list[dict]:
    # Backfill summaries for chats created before the memory feature, or when
    # model-based summarization was unavailable. This is local and deterministic.
    with _connection() as con:
        missing = con.execute(
            """
            SELECT c.id AS conversation_id
            FROM conversations AS c
            LEFT JOIN conversation_memories AS m
                ON m.conversation_id = c.id AND m.owner_id = c.owner_id
            WHERE c.owner_id = ? AND m.conversation_id IS NULL
            ORDER BY c.updated_at DESC
            """,
            (owner_id,),
        ).fetchall()
    for row in missing:
        messages = get_recent_messages(owner_id, row["conversation_id"], limit=40)
        fallback = fallback_memory_summary(messages)
        if fallback:
            update_conversation_memory(owner_id, row["conversation_id"], fallback)

    with _connection() as con:
        rows = con.execute(
            """
            SELECT c.id AS conversation_id, c.title, m.summary, m.updated_at
            FROM conversation_memories AS m
            JOIN conversations AS c ON c.id = m.conversation_id
            WHERE m.owner_id = ? AND c.owner_id = ? AND m.summary <> ''
            ORDER BY m.updated_at DESC
            """,
            (owner_id, owner_id),
        ).fetchall()
    return [dict(row) for row in rows]


def clear_conversation_memories(owner_id: str) -> int:
    with _connection() as con:
        cursor = con.execute(
            "DELETE FROM conversation_memories WHERE owner_id = ?",
            (owner_id,),
        )
        return cursor.rowcount


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


# ---------------------------------------------------------
# Read-only conversation sharing
# ---------------------------------------------------------

SHARE_EXPIRY_DAYS = 7


def _share_safe_citations(value) -> list[dict]:
    """Only expose selected citation fields in shared snapshots."""
    if not isinstance(value, list):
        return []

    allowed = ("id", "title", "url", "type", "page", "excerpt")
    result = []

    for item in value[:20]:
        if not isinstance(item, dict):
            continue

        citation = {}

        for key in allowed:
            field = item.get(key)

            if isinstance(field, (str, int, float)) and not isinstance(field, bool):
                citation[key] = field

        result.append(citation)

    return result


def create_share_link(owner_id: str, conversation_id: str) -> dict | None:
    """Create a seven-day read-only snapshot share link."""
    conversation = get_conversation(owner_id, conversation_id)

    if conversation is None:
        return None

    messages = get_messages(owner_id, conversation_id)

    snapshot = {
        "conversation": {
            "title": conversation.get("title") or "Shared conversation",
            "created_at": conversation.get("created_at"),
            "updated_at": conversation.get("updated_at"),
        },
        "messages": [],
    }

    for message in messages:
        snapshot["messages"].append(
            {
                "role": message.get("role", "assistant"),
                "content": str(message.get("content", "")),
                "source_used": str(message.get("source_used", "")),
                "citations": _share_safe_citations(message.get("citations", [])),
                "created_at": message.get("created_at"),
            }
        )

    share_id = uuid.uuid4().hex
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(days=SHARE_EXPIRY_DAYS)

    created_text = created_at.isoformat()
    expires_text = expires_at.isoformat()

    with _connection() as con:
        # Verify ownership again when creating the share.
        owned = con.execute(
            """
            SELECT 1
            FROM conversations
            WHERE id = ? AND owner_id = ?
            """,
            (conversation_id, owner_id),
        ).fetchone()

        if owned is None:
            return None
        # Revoke previous links for this conversation before creating a new one.
        con.execute(
            """
    UPDATE conversation_shares
    SET revoked_at = ?
    WHERE conversation_id = ?
      AND owner_id = ?
      AND revoked_at IS NULL
    """,
            (created_text, conversation_id, owner_id),
        )
        con.execute(
            """
            INSERT INTO conversation_shares (
                id,
                conversation_id,
                owner_id,
                token_hash,
                snapshot_json,
                created_at,
                expires_at,
                revoked_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                share_id,
                conversation_id,
                owner_id,
                token_hash,
                json.dumps(snapshot, ensure_ascii=False),
                created_text,
                expires_text,
            ),
        )

    # The raw token is returned only once; only its hash is stored.
    return {
        "share_id": share_id,
        "token": token,
        "created_at": created_text,
        "expires_at": expires_text,
    }


def get_shared_conversation(token: str) -> dict | None:
    """Read an active shared snapshot without exposing owner data or traces."""
    if not isinstance(token, str) or not (20 <= len(token) <= 128):
        return None

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    with _connection() as con:
        row = con.execute(
            """
            SELECT snapshot_json, created_at, expires_at
            FROM conversation_shares
            WHERE token_hash = ?
              AND revoked_at IS NULL
              AND expires_at > ?
            """,
            (token_hash, _now()),
        ).fetchone()

    if row is None:
        return None

    try:
        snapshot = json.loads(row["snapshot_json"])
    except (TypeError, json.JSONDecodeError):
        return None

    if not isinstance(snapshot, dict):
        return None

    snapshot["share_created_at"] = row["created_at"]
    snapshot["expires_at"] = row["expires_at"]

    return snapshot


def list_share_links(
    owner_id: str,
    conversation_id: str,
) -> list[dict] | None:
    """List share-link status for a conversation owned by this user."""
    with _connection() as con:
        owned = con.execute(
            """
            SELECT 1
            FROM conversations
            WHERE id = ? AND owner_id = ?
            """,
            (conversation_id, owner_id),
        ).fetchone()

        if owned is None:
            return None

        rows = con.execute(
            """
            SELECT id, created_at, expires_at, revoked_at
            FROM conversation_shares
            WHERE conversation_id = ? AND owner_id = ?
            ORDER BY created_at DESC
            """,
            (conversation_id, owner_id),
        ).fetchall()

    return [dict(row) for row in rows]


def revoke_share_link(
    owner_id: str,
    conversation_id: str,
    share_id: str,
) -> bool:
    """Revoke a share link belonging to the signed-in owner."""
    with _connection() as con:
        cursor = con.execute(
            """
            UPDATE conversation_shares
            SET revoked_at = ?
            WHERE id = ?
              AND conversation_id = ?
              AND owner_id = ?
              AND revoked_at IS NULL
            """,
            (_now(), share_id, conversation_id, owner_id),
        )

    return cursor.rowcount > 0
