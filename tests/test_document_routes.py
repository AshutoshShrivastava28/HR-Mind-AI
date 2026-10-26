import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth_routes import router as auth_router
from app.core.config import get_settings
from app.services.authentication import auth_connection, create_user, init_auth_db
from app.services.conversations import init_conversation_db
from app.services.upload_validation import validate_file

# Mock external services before importing the API routes.
fake_vectorstore = types.ModuleType("app.rag.vectorstore")
fake_workflow = types.ModuleType("app.rag.workflow")
fake_ingestion = types.ModuleType("app.services.ingestion")
fake_audit = types.ModuleType("app.services.audit")

fake_audit.write_audit = lambda *_args, **_kwargs: None
fake_audit.init_db = lambda: None


class DummyDocument:
    def __init__(self, content, source="upload"):
        self.page_content = content
        self.metadata = {"source": source}


def fake_load_file(path):
    validate_file(path)
    return [DummyDocument(path.read_text(encoding="utf-8"), path.name)]


def fake_chunk_documents(documents):
    return list(documents)


fake_ingestion.SUPPORTED = {".pdf", ".txt", ".md", ".docx"}
fake_ingestion.load_file = fake_load_file
fake_ingestion.chunk_documents = fake_chunk_documents


class FakeGraph:
    def stream(self, *_args, **_kwargs):
        yield {
            "direct_answer": {
                "answer": "test",
                "source_used": "direct_answer",
            }
        }


def fake_answer_guest(question):
    return {
        "answer": "Public guest answer",
        "source_used": "direct_answer",
        "citations": [],
        "trace": ["Guest mode -> direct test response"],
    }


fake_workflow.agent_graph = FakeGraph()
fake_workflow.answer_guest_question = fake_answer_guest

fake_vectorstore.indexed = []
fake_vectorstore.deleted = []
fake_vectorstore.get_retriever_calls = []


def fake_add_documents(chunks, namespace=None, ids=None):
    if ids is None or len(ids) != len(chunks):
        raise AssertionError("Explicit vector IDs matching chunks are required.")

    fake_vectorstore.indexed.extend(
        [
            (namespace, vector_id, chunk.page_content)
            for vector_id, chunk in zip(ids, chunks)
        ]
    )
    return ids


def fake_delete_vectors(ids, namespace=None):
    fake_vectorstore.deleted.append((list(ids), namespace))
    ids_to_delete = set(ids)

    fake_vectorstore.indexed = [
        item
        for item in fake_vectorstore.indexed
        if not (item[0] == namespace and item[1] in ids_to_delete)
    ]


fake_vectorstore.add_documents = fake_add_documents
fake_vectorstore.delete_vectors = fake_delete_vectors
fake_vectorstore.get_retriever = (
    lambda namespace=None: fake_vectorstore.get_retriever_calls.append(namespace)
)

# Provide the Answer Quality functions imported by app.api.routes.
# These stubs keep document-route tests independent of the real audit database.
fake_audit.record_answer_quality_event = lambda **kwargs: kwargs.get("event_id")

fake_audit.submit_answer_feedback = lambda *args, **kwargs: "submitted"

fake_audit.record_source_engagement = lambda *args, **kwargs: True

fake_audit.get_answer_quality_dashboard = lambda *args, **kwargs: {}
sys.modules["app.services.audit"] = fake_audit
sys.modules["app.services.ingestion"] = fake_ingestion
sys.modules["app.rag.vectorstore"] = fake_vectorstore
sys.modules["app.rag.workflow"] = fake_workflow

# Import after installing the fake adapters.
routes_module = importlib.import_module("app.api.routes")


class DocumentRouteSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        settings = get_settings()

        settings.auth_db_path = str(root / "auth.db")
        settings.conversation_db_path = str(root / "conversations.db")
        settings.upload_dir = str(root / "uploads")
        settings.app_env = "development"
        settings.admin_email = "admin@example.test"

        Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)

        fake_vectorstore.indexed = []
        fake_vectorstore.deleted = []
        fake_vectorstore.get_retriever_calls = []

        init_auth_db()
        init_conversation_db()

        app = FastAPI()
        app.include_router(auth_router)
        app.include_router(routes_module.router)

        self.admin_client = TestClient(app, base_url="http://localhost")
        self.employee_a_client = TestClient(app, base_url="http://localhost")
        self.employee_b_client = TestClient(app, base_url="http://localhost")
        self.guest_client = TestClient(app, base_url="http://localhost")

        self.admin = self.make_user(
            "Admin Tester",
            "admin@example.test",
            "AdminPassword!2026",
            "admin",
        )
        self.employee_a = self.make_user(
            "Employee A",
            "employee-a@example.test",
            "EmployeePasswordA!2026",
            "employee",
        )
        self.employee_b = self.make_user(
            "Employee B",
            "employee-b@example.test",
            "EmployeePasswordB!2026",
            "employee",
        )

        self.login(self.admin_client, self.admin, "AdminPassword!2026")
        self.login(
            self.employee_a_client,
            self.employee_a,
            "EmployeePasswordA!2026",
        )
        self.login(
            self.employee_b_client,
            self.employee_b,
            "EmployeePasswordB!2026",
        )

    def tearDown(self):
        self.admin_client.close()
        self.employee_a_client.close()
        self.employee_b_client.close()
        self.guest_client.close()
        self.temp.cleanup()

    def make_user(self, name, email, password, role):
        user, _token = create_user(name, email, password)

        with auth_connection() as db:
            db.execute(
                "UPDATE users SET role = ?, is_verified = 1 WHERE id = ?",
                (role, user["id"]),
            )

        return {
            **user,
            "role": role,
            "is_verified": True,
        }

    def login(self, client, user, password):
        response = client.post(
            "/api/auth/login",
            json={"email": user["email"], "password": password},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_personal_documents_are_private_to_the_uploader(self):
        response = self.employee_a_client.post(
            "/api/documents/personal",
            files={
                "file": (
                    "private.txt",
                    b"Employee A private test data",
                    "text/plain",
                )
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        document_id = response.json()["id"]

        list_a = self.employee_a_client.get("/api/documents/personal")
        list_b = self.employee_b_client.get("/api/documents/personal")

        self.assertEqual(list_a.status_code, 200)
        self.assertEqual(list_b.status_code, 200)

        self.assertEqual(
            [item["id"] for item in list_a.json()["documents"]],
            [document_id],
        )
        self.assertEqual(list_b.json()["documents"], [])

        # A different employee cannot delete another user's document.
        response = self.employee_b_client.delete(
            f"/api/documents/personal/{document_id}"
        )
        self.assertEqual(response.status_code, 404)

    def test_employee_cannot_access_or_upload_company_documents(self):
        response = self.employee_a_client.get("/api/documents/company")
        self.assertEqual(response.status_code, 403)

        response = self.employee_a_client.post(
            "/api/ingest",
            files={"file": ("company.txt", b"shared policy", "text/plain")},
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_upload_shared_company_document(self):
        response = self.admin_client.post(
            "/api/ingest",
            files={
                "file": (
                    "company-policy.txt",
                    b"Shared leave policy text",
                    "text/plain",
                )
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(fake_vectorstore.indexed)

        self.assertTrue(
            all(
                namespace == get_settings().pinecone_namespace
                for namespace, _vector_id, _content in fake_vectorstore.indexed
            )
        )

        response = self.admin_client.get("/api/documents/company")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["documents"]), 1)

        response = self.employee_a_client.get("/api/documents/company")
        self.assertEqual(response.status_code, 403)

    def test_upload_size_limit_removes_partial_file(self):
        root = Path(get_settings().upload_dir)
        before = set(root.rglob("*"))
        oversized = b"x" * (10 * 1024 * 1024 + 1)

        response = self.employee_a_client.post(
            "/api/documents/personal",
            files={"file": ("oversized.txt", oversized, "text/plain")},
        )
        self.assertEqual(response.status_code, 413, response.text)

        after = set(root.rglob("*"))
        new_paths = after - before

        self.assertFalse(
            any(path.is_file() for path in new_paths),
            new_paths,
        )

    def test_vector_deletion_failure_preserves_document_and_file(self):
        response = self.employee_a_client.post(
            "/api/documents/personal",
            files={
                "file": (
                    "preserve.txt",
                    b"retryable delete test",
                    "text/plain",
                )
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        document_id = response.json()["id"]

        storage_candidates = list(
            (Path(get_settings().upload_dir) / "users" / self.employee_a["id"]).rglob(
                "preserve.txt"
            )
        )
        self.assertEqual(len(storage_candidates), 1)

        with patch.object(
            routes_module,
            "delete_vectors",
            side_effect=RuntimeError("simulated vector deletion failure"),
        ):
            response = self.employee_a_client.delete(
                f"/api/documents/personal/{document_id}"
            )

        self.assertEqual(response.status_code, 503, response.text)

        refreshed = self.employee_a_client.get("/api/documents/personal")
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(
            [item["id"] for item in refreshed.json()["documents"]],
            [document_id],
        )
        self.assertTrue(storage_candidates[0].exists())

    def test_upload_rejects_pdf_extension_with_invalid_content(self):
        response = self.employee_a_client.post(
            "/api/documents/personal",
            files={"file": ("fake.pdf", b"not a PDF", "application/pdf")},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(fake_vectorstore.indexed, [])

    def test_metadata_failure_rolls_back_vectors_and_uploaded_file(self):
        before = set(Path(get_settings().upload_dir).rglob("*"))

        with patch.object(
            routes_module,
            "create_personal_document",
            side_effect=RuntimeError("simulated database failure"),
        ):
            response = self.employee_a_client.post(
                "/api/documents/personal",
                files={
                    "file": (
                        "rollback.txt",
                        b"temporary private content",
                        "text/plain",
                    )
                },
            )

        self.assertEqual(response.status_code, 500)
        self.assertTrue(fake_vectorstore.deleted)
        self.assertEqual(fake_vectorstore.indexed, [])

        after = set(Path(get_settings().upload_dir).rglob("*"))
        new_paths = after - before

        self.assertFalse(
            any(path.is_file() for path in new_paths),
            new_paths,
        )

    def test_guest_chat_does_not_retrieve_private_vectors(self):
        with auth_connection() as db:
            before = db.execute("SELECT COUNT(*) FROM personal_documents").fetchone()[0]

        response = self.guest_client.post(
            "/api/guest/chat",
            json={"question": "What is onboarding?"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Public guest answer", response.text)
        self.assertEqual(fake_vectorstore.get_retriever_calls, [])

        with auth_connection() as db:
            after = db.execute("SELECT COUNT(*) FROM personal_documents").fetchone()[0]

        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
