"""Regression tests for approved policy selection and ambiguous metadata."""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.rag.policy_lifecycle import filter_company_policy_candidates


def document(document_id, filename, content="Policy excerpt"):
    return SimpleNamespace(page_content=content, metadata={
        "hrmindai_document_id": document_id,
        "source": filename,
        "hrmindai_document_kind": "company",
    })


class PolicyLifecycleRetrievalTests(unittest.TestCase):
    def test_active_version_wins_over_superseded_and_draft_versions(self):
        policies = {
            "remote-v1": {"document_id": "remote-v1", "filename": "Remote_Work_v1.docx", "policy_family": "Remote Work", "version_label": "v1.0", "policy_status": "superseded", "effective_date": "2026-01-01"},
            "remote-v2": {"document_id": "remote-v2", "filename": "Remote_Work_v2.docx", "policy_family": "Remote Work", "version_label": "v2.0", "policy_status": "active", "effective_date": "2026-04-01"},
            "remote-v3-draft": {"document_id": "remote-v3-draft", "filename": "Remote_Work_v3_draft.docx", "policy_family": "Remote Work", "version_label": "v3.0-draft", "policy_status": "draft", "effective_date": "2026-06-01"},
        }
        candidates = [document("remote-v1", "Remote_Work_v1.docx"), document("remote-v3-draft", "Remote_Work_v3_draft.docx"), document("remote-v2", "Remote_Work_v2.docx")]
        selected, stats = filter_company_policy_candidates(candidates, policies, limit=4)
        self.assertEqual([item.metadata["hrmindai_document_id"] for item in selected], ["remote-v2"])
        self.assertEqual(selected[0].metadata["hrmindai_policy_status"], "active")
        self.assertFalse(selected[0].metadata["hrmindai_policy_conflict"])
        self.assertEqual(stats["excluded_draft_or_superseded"], 2)

    def test_two_active_versions_flag_ambiguity_even_if_only_one_is_retrieved(self):
        policies = {
            "remote-v1": {"document_id": "remote-v1", "filename": "Remote_Work_v1.docx", "policy_family": "Remote Work", "version_label": "v1.0", "policy_status": "active", "effective_date": "2026-01-01"},
            "remote-v2": {"document_id": "remote-v2", "filename": "Remote_Work_v2.docx", "policy_family": " remote   work ", "version_label": "v2.0", "policy_status": "active", "effective_date": "2026-04-01"},
        }
        # Simulate vector search returning only v2: metadata registry still exposes both Active versions.
        selected, stats = filter_company_policy_candidates([document("remote-v2", "Remote_Work_v2.docx")], policies, limit=4)
        self.assertEqual(len(selected), 1)
        self.assertTrue(selected[0].metadata["hrmindai_policy_conflict"])
        summary = selected[0].metadata["hrmindai_policy_conflict_summary"]
        self.assertIn("Remote_Work_v1.docx", summary)
        self.assertIn("Remote_Work_v2.docx", summary)
        self.assertIn("authoritative version is ambiguous", summary)
        self.assertEqual(stats["conflict_families"], 1)

    def test_active_version_excludes_unreviewed_same_family_but_keeps_unrelated_legacy_evidence(self):
        policies = {
            "remote-v1": {"document_id": "remote-v1", "filename": "Remote_Work_v1.docx", "policy_family": "Remote Work", "version_label": "v1.0", "policy_status": "active", "effective_date": "2026-04-01"},
            "remote-old": {"document_id": "remote-old", "filename": "Remote_Work_legacy.docx", "policy_family": "Remote Work", "version_label": "legacy", "policy_status": "unreviewed", "effective_date": ""},
            "handbook": {"document_id": "handbook", "filename": "General_Handbook.docx", "policy_family": "", "version_label": "", "policy_status": "unreviewed", "effective_date": ""},
        }
        candidates = [document("remote-old", "Remote_Work_legacy.docx"), document("handbook", "General_Handbook.docx"), document("remote-v1", "Remote_Work_v1.docx")]
        selected, stats = filter_company_policy_candidates(candidates, policies, limit=4)
        ids = [item.metadata["hrmindai_document_id"] for item in selected]
        self.assertEqual(ids, ["remote-v1", "handbook"])
        self.assertEqual(stats["excluded_by_active_family"], 1)
        self.assertEqual(selected[1].metadata["hrmindai_policy_status"], "unreviewed")


if __name__ == "__main__":
    unittest.main()
