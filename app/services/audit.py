import json
import sqlite3
from datetime import datetime, timezone
from app.core.config import get_settings

settings = get_settings()


def init_db() -> None:
    con = sqlite3.connect(settings.audit_db_path)
    con.execute("""CREATE TABLE IF NOT EXISTS query_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            question TEXT NOT NULL,
            source_used TEXT NOT NULL,
            trace_json TEXT NOT NULL
        )""")
    con.commit()
    con.close()
    _ensure_quality_table()


def write_audit(question: str, source_used: str, trace: list[str]) -> None:
    con = sqlite3.connect(settings.audit_db_path)
    con.execute(
        "INSERT INTO query_audit(created_at, question, source_used, trace_json) VALUES (?, ?, ?, ?)",
        (
            datetime.now(timezone.utc).isoformat(),
            question,
            source_used,
            json.dumps(trace),
        ),
    )
    con.commit()
    con.close()

# HRMIND_AI_ANSWER_QUALITY_V1
# Analytics stores topic labels and retrieval metadata only. It does not copy
# raw prompts or generated answers into this table.
_QUALITY_TOPICS = (
    "Leave & attendance",
    "Remote & flexible work",
    "Travel & expenses",
    "Benefits & payroll",
    "Hiring & onboarding",
    "Performance & conduct",
    "Other HR questions",
)


def _ensure_quality_table() -> None:
    con = sqlite3.connect(settings.audit_db_path, timeout=10)
    try:
        con.execute(
            """CREATE TABLE IF NOT EXISTS answer_quality_events (
                event_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                user_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                source_used TEXT NOT NULL,
                citation_count INTEGER NOT NULL DEFAULT 0,
                evidence_gap INTEGER NOT NULL DEFAULT 0,
                source_engaged INTEGER NOT NULL DEFAULT 0,
                feedback_rating TEXT,
                feedback_reason TEXT,
                feedback_at TEXT,
                CHECK (feedback_rating IS NULL OR feedback_rating IN ('helpful', 'not_helpful'))
            )"""
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_answer_quality_created ON answer_quality_events(created_at)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_answer_quality_topic ON answer_quality_events(topic, created_at)"
        )
        con.commit()
    finally:
        con.close()


def _question_topic(question: str) -> str:
    """Classify a question without storing the question text itself."""
    text = " ".join(str(question or "").lower().split())
    if any(term in text for term in ("remote work", "work from home", "wfh", "hybrid", "flexible work", "remote day")):
        return "Remote & flexible work"
    if any(term in text for term in ("leave", "holiday", "attendance", "sick day", "sick leave", "absence", "carry forward", "time off")):
        return "Leave & attendance"
    if any(term in text for term in ("travel", "expense", "hotel", "meal allowance", "reimburse", "per diem", "business trip", "mileage")):
        return "Travel & expenses"
    if any(term in text for term in ("payroll", "salary", "payslip", "pay slip", "benefit", "insurance", "pension", "compensation", "bonus")):
        return "Benefits & payroll"
    if any(term in text for term in ("onboarding", "probation", "new hire", "joining", "recruit", "offer letter", "notice period")):
        return "Hiring & onboarding"
    if any(term in text for term in ("performance", "conduct", "disciplinary", "harassment", "grievance", "complaint", "code of conduct")):
        return "Performance & conduct"
    return "Other HR questions"


def record_answer_quality_event(
    event_id: str,
    user_id: str,
    question: str,
    source_used: str,
    citation_count: int,
    kb_grade: str = "",
    web_grade: str = "",
) -> None:
    """Record privacy-conscious telemetry for a completed signed-in answer."""
    from datetime import datetime, timezone

    _ensure_quality_table()
    source = str(source_used or "unknown").strip().lower()[:60]
    citations = max(0, int(citation_count or 0))
    kb = str(kb_grade or "").strip().lower()
    web = str(web_grade or "").strip().lower()
    evidence_gap = int(
        "insufficient" in source
        or source in {"weak_evidence", "no_evidence"}
        or (source in {"private_kb", "company_kb", "personal_kb", "web_search", "external_web_search"} and citations == 0)
        or (kb == "weak" and web == "weak")
    )
    con = sqlite3.connect(settings.audit_db_path, timeout=10)
    try:
        con.execute(
            """INSERT INTO answer_quality_events
               (event_id, created_at, user_id, topic, source_used, citation_count, evidence_gap)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                str(event_id),
                datetime.now(timezone.utc).isoformat(),
                str(user_id),
                _question_topic(question),
                source,
                citations,
                evidence_gap,
            ),
        )
        con.commit()
    finally:
        con.close()


def submit_answer_feedback(event_id: str, user_id: str, rating: str, reason: str | None = None) -> str:
    """A user may rate only their own answer event, once."""
    from datetime import datetime, timezone

    _ensure_quality_table()
    con = sqlite3.connect(settings.audit_db_path, timeout=10)
    try:
        row = con.execute(
            "SELECT feedback_rating FROM answer_quality_events WHERE event_id = ? AND user_id = ?",
            (str(event_id), str(user_id)),
        ).fetchone()
        if row is None:
            return "not_found"
        if row[0] is not None:
            return "already_rated"
        cur = con.execute(
            """UPDATE answer_quality_events
               SET feedback_rating = ?, feedback_reason = ?, feedback_at = ?
               WHERE event_id = ? AND user_id = ? AND feedback_rating IS NULL""",
            (
                rating,
                (str(reason).strip()[:40] if reason else None),
                datetime.now(timezone.utc).isoformat(),
                str(event_id),
                str(user_id),
            ),
        )
        con.commit()
        return "saved" if cur.rowcount == 1 else "already_rated"
    finally:
        con.close()


def record_source_engagement(event_id: str, user_id: str) -> bool:
    """Record at least one source/citation interaction for this owned answer."""
    _ensure_quality_table()
    con = sqlite3.connect(settings.audit_db_path, timeout=10)
    try:
        cur = con.execute(
            """UPDATE answer_quality_events SET source_engaged = 1
               WHERE event_id = ? AND user_id = ? AND citation_count > 0""",
            (str(event_id), str(user_id)),
        )
        con.commit()
        return cur.rowcount == 1
    finally:
        con.close()


def get_answer_quality_dashboard(days: int = 30) -> dict:
    """Return admin-ready aggregates; never return raw questions, answers, or user IDs."""
    from datetime import datetime, timedelta, timezone

    _ensure_quality_table()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    con = sqlite3.connect(settings.audit_db_path, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        totals = con.execute(
            """SELECT COUNT(*) AS total_answers,
                      SUM(CASE WHEN feedback_rating IS NOT NULL THEN 1 ELSE 0 END) AS feedback_responses,
                      SUM(CASE WHEN feedback_rating = 'helpful' THEN 1 ELSE 0 END) AS helpful_count,
                      SUM(CASE WHEN feedback_rating = 'not_helpful' THEN 1 ELSE 0 END) AS not_helpful_count,
                      SUM(CASE WHEN source_used != 'direct' THEN 1 ELSE 0 END) AS retrieval_answers,
                      SUM(CASE WHEN citation_count > 0 THEN 1 ELSE 0 END) AS answers_with_citations,
                      SUM(CASE WHEN citation_count > 0 AND source_engaged = 1 THEN 1 ELSE 0 END) AS source_engaged_count,
                      SUM(CASE WHEN evidence_gap = 1 THEN 1 ELSE 0 END) AS evidence_gaps
               FROM answer_quality_events WHERE created_at >= ?""",
            (cutoff,),
        ).fetchone()

        def number(key: str) -> int:
            return int(totals[key] or 0)

        def percentage(numerator: int, denominator: int) -> float:
            return round((100.0 * numerator / denominator), 1) if denominator else 0.0

        total_answers = number("total_answers")
        feedback_responses = number("feedback_responses")
        helpful_count = number("helpful_count")
        not_helpful_count = number("not_helpful_count")
        retrieval_answers = number("retrieval_answers")
        answers_with_citations = number("answers_with_citations")
        source_engaged_count = number("source_engaged_count")
        evidence_gaps = number("evidence_gaps")

        topic_rows = con.execute(
            """SELECT topic, COUNT(*) AS total,
                      SUM(CASE WHEN feedback_rating = 'helpful' THEN 1 ELSE 0 END) AS helpful_count,
                      SUM(CASE WHEN feedback_rating = 'not_helpful' THEN 1 ELSE 0 END) AS not_helpful_count,
                      SUM(CASE WHEN evidence_gap = 1 THEN 1 ELSE 0 END) AS evidence_gaps,
                      SUM(CASE WHEN citation_count > 0 THEN 1 ELSE 0 END) AS answers_with_citations
               FROM answer_quality_events WHERE created_at >= ?
               GROUP BY topic ORDER BY total DESC, topic ASC""",
            (cutoff,),
        ).fetchall()
        topics = []
        for row in topic_rows:
            item = dict(row)
            item["helpful_count"] = int(item["helpful_count"] or 0)
            item["not_helpful_count"] = int(item["not_helpful_count"] or 0)
            item["evidence_gaps"] = int(item["evidence_gaps"] or 0)
            item["answers_with_citations"] = int(item["answers_with_citations"] or 0)
            topics.append(item)

        reason_rows = con.execute(
            """SELECT feedback_reason AS reason, COUNT(*) AS total
               FROM answer_quality_events
               WHERE created_at >= ? AND feedback_rating = 'not_helpful' AND feedback_reason IS NOT NULL AND feedback_reason != ''
               GROUP BY feedback_reason ORDER BY total DESC""",
            (cutoff,),
        ).fetchall()
        trend_rows = con.execute(
            """SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS answers,
                      SUM(CASE WHEN feedback_rating = 'helpful' THEN 1 ELSE 0 END) AS helpful,
                      SUM(CASE WHEN evidence_gap = 1 THEN 1 ELSE 0 END) AS evidence_gaps
               FROM answer_quality_events WHERE created_at >= ?
               GROUP BY substr(created_at, 1, 10) ORDER BY day ASC""",
            (cutoff,),
        ).fetchall()
        recent_rows = con.execute(
            """SELECT feedback_at AS created_at, topic, feedback_rating AS rating,
                      feedback_reason AS reason, source_used
               FROM answer_quality_events
               WHERE created_at >= ? AND feedback_rating IS NOT NULL
               ORDER BY feedback_at DESC LIMIT 20""",
            (cutoff,),
        ).fetchall()

        return {
            "days": days,
            "summary": {
                "total_answers": total_answers,
                "feedback_responses": feedback_responses,
                "feedback_response_rate": percentage(feedback_responses, total_answers),
                "helpful_count": helpful_count,
                "not_helpful_count": not_helpful_count,
                "helpfulness_rate": percentage(helpful_count, feedback_responses),
                "retrieval_answers": retrieval_answers,
                "answers_with_citations": answers_with_citations,
                "citation_coverage_rate": percentage(answers_with_citations, retrieval_answers),
                "source_engaged_count": source_engaged_count,
                "source_engagement_rate": percentage(source_engaged_count, answers_with_citations),
                "evidence_gaps": evidence_gaps,
                "evidence_gap_rate": percentage(evidence_gaps, retrieval_answers),
            },
            "topics": topics,
            "evidence_gaps_by_topic": [
                {"topic": item["topic"], "count": item["evidence_gaps"]}
                for item in topics if item["evidence_gaps"] > 0
            ],
            "feedback_reasons": [dict(row) for row in reason_rows],
            "trend": [
                {"day": row["day"], "answers": int(row["answers"] or 0),
                 "helpful": int(row["helpful"] or 0), "evidence_gaps": int(row["evidence_gaps"] or 0)}
                for row in trend_rows
            ],
            "recent_feedback": [dict(row) for row in recent_rows],
            "privacy_note": "Analytics uses topic labels and retrieval metadata; raw questions, generated answers, and user identifiers are not returned by this dashboard.",
        }
    finally:
        con.close()
