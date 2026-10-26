import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.services import audit


class AnswerQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = audit.settings.audit_db_path
        audit.settings.audit_db_path = str(Path(self.temp.name) / "audit.db")
        audit.init_db()

    def tearDown(self):
        audit.settings.audit_db_path = self.old_path
        self.temp.cleanup()

    def test_topic_label_is_stored_without_raw_question(self):
        audit.record_answer_quality_event(
            "event-1", "user-1",
            "How much leave does Alex Example have? alex@example.com",
            "insufficient_evidence", 0,
        )
        con = sqlite3.connect(audit.settings.audit_db_path)
        try:
            columns = {row[1] for row in con.execute("PRAGMA table_info(answer_quality_events)")}
            self.assertNotIn("question", columns)
            self.assertNotIn("answer", columns)
            row = con.execute(
                "SELECT topic, evidence_gap FROM answer_quality_events WHERE event_id = ?",
                ("event-1",),
            ).fetchone()
            self.assertEqual(row, ("Leave & attendance", 1))
        finally:
            con.close()

    def test_feedback_is_owner_scoped_and_single_submission(self):
        audit.record_answer_quality_event("event-2", "user-1", "Remote work days?", "private_kb", 2)
        self.assertEqual(audit.submit_answer_feedback("event-2", "user-2", "helpful"), "not_found")
        self.assertEqual(audit.submit_answer_feedback("event-2", "user-1", "not_helpful", "citation_issue"), "saved")
        self.assertEqual(audit.submit_answer_feedback("event-2", "user-1", "helpful"), "already_rated")

    def test_dashboard_aggregates_answers_feedback_and_source_engagement(self):
        audit.record_answer_quality_event("event-3", "user-1", "hotel reimbursement", "private_kb", 1)
        audit.record_answer_quality_event("event-4", "user-2", "latest payroll benefit", "insufficient_evidence", 0)
        self.assertTrue(audit.record_source_engagement("event-3", "user-1"))
        self.assertEqual(audit.submit_answer_feedback("event-3", "user-1", "helpful"), "saved")
        result = audit.get_answer_quality_dashboard(30)
        self.assertEqual(result["summary"]["total_answers"], 2)
        self.assertEqual(result["summary"]["feedback_responses"], 1)
        self.assertEqual(result["summary"]["source_engaged_count"], 1)
        self.assertEqual(result["summary"]["evidence_gaps"], 1)
        self.assertEqual(result["summary"]["retrieval_answers"], 2)
        self.assertEqual(result["summary"]["citation_coverage_rate"], 50.0)
        self.assertEqual(result["summary"]["evidence_gap_rate"], 50.0)
        self.assertNotIn("user_id", str(result))
        self.assertNotIn("hotel reimbursement", str(result))


if __name__ == "__main__":
    unittest.main()
