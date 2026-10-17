import sys
import tempfile
import types
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth_routes import router as auth_router
from app.core.config import get_settings
from app.services.authentication import (
    auth_connection,
    create_company_document,
    create_personal_document,
    create_user,
    init_auth_db,
)
from app.services.conversations import init_conversation_db


class DeletionEdgeCaseTests(unittest.TestCase):
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
        init_auth_db()
        init_conversation_db()

        self.fake_vectorstore = types.ModuleType("app.rag.vectorstore")
        self.fake_vectorstore.deleted = []

        def fake_delete_vectors(ids, namespace=None):
            self.fake_vectorstore.deleted.append((list(ids), namespace))

        self.fake_vectorstore.delete_vectors = fake_delete_vectors
        self.previous_vectorstore = sys.modules.get("app.rag.vectorstore")
        sys.modules["app.rag.vectorstore"] = self.fake_vectorstore

        app = FastAPI()
        app.include_router(auth_router)

        self.admin_client = TestClient(app, base_url="http://localhost")
        self.admin = self.make_user(
            "Admin Tester",
            "admin@example.test",
            "AdminPassword!2026",
            "admin",
        )
        self.employee = self.make_user(
            "Employee Tester",
            "employee@example.test",
            "EmployeePassword!2026",
            "employee",
        )

        response = self.admin_client.post(
            "/api/auth/login",
            json={
                "email": self.admin["email"],
                "password": "AdminPassword!2026",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def tearDown(self):
        self.admin_client.close()

        if self.previous_vectorstore is None:
            sys.modules.pop("app.rag.vectorstore", None)
        else:
            sys.modules["app.rag.vectorstore"] = self.previous_vectorstore

        self.temp.cleanup()

    def make_user(self, name, email, password, role):
        user, _token = create_user(name, email, password)

        with auth_connection() as db:
            db.execute(
                """
                UPDATE users
                SET role = ?, is_verified = 1
                WHERE id = ?
                """,
                (role, user["id"]),
            )

        return {
            **user,
            "role": role,
            "is_verified": True,
        }

    def test_vector_deletion_failure_preserves_account_and_document(self):
        settings = get_settings()

        private_dir = (
            Path(settings.upload_dir) / "users" / self.employee["id"] / "document-1"
        )
        private_dir.mkdir(parents=True, exist_ok=True)

        private_file = private_dir / "private.txt"
        private_file.write_text("private test data", encoding="utf-8")

        create_personal_document(
            "document-1",
            self.employee["id"],
            "private.txt",
            str(private_file),
            f"user-{self.employee['id']}",
            ["private-vector-1"],
            private_file.stat().st_size,
        )

        def fail_vector_deletion(ids, namespace=None):
            raise RuntimeError("Simulated Pinecone failure")

        self.fake_vectorstore.delete_vectors = fail_vector_deletion

        response = self.admin_client.delete(
            f"/api/auth/admin/users/{self.employee['id']}"
        )
        self.assertEqual(response.status_code, 503, response.text)

        with auth_connection() as db:
            user = db.execute(
                "SELECT id FROM users WHERE id = ?",
                (self.employee["id"],),
            ).fetchone()

            document = db.execute(
                "SELECT id FROM personal_documents WHERE id = ?",
                ("document-1",),
            ).fetchone()

        self.assertIsNotNone(user)
        self.assertIsNotNone(document)
        self.assertTrue(private_file.exists())

    def test_company_document_is_retained_when_uploader_is_deleted(self):
        settings = get_settings()
        company_dir = Path(settings.upload_dir) / "company" / "shared-document"
        company_dir.mkdir(parents=True, exist_ok=True)

        company_file = company_dir / "policy.txt"
        company_file.write_text("Shared company policy", encoding="utf-8")

        create_company_document(
            "shared-document",
            "policy.txt",
            str(company_file),
            ["shared-vector-1"],
            company_file.stat().st_size,
            self.employee["id"],
        )

        response = self.admin_client.delete(
            f"/api/auth/admin/users/{self.employee['id']}"
        )
        self.assertEqual(response.status_code, 200, response.text)

        with auth_connection() as db:
            company_doc = db.execute(
                """
                SELECT uploaded_by, storage_path
                FROM company_documents
                WHERE id = ?
                """,
                ("shared-document",),
            ).fetchone()

        self.assertIsNotNone(company_doc)
        self.assertEqual(company_doc["uploaded_by"], self.admin["id"])
        self.assertEqual(company_doc["storage_path"], str(company_file))
        self.assertTrue(company_file.exists())

    def test_remote_http_authentication_is_rejected(self):
        remote_client = TestClient(
            self.admin_client.app,
            base_url="http://remote.example.test",
        )
        try:
            response = remote_client.get("/api/auth/me")
            self.assertEqual(response.status_code, 426, response.text)
        finally:
            remote_client.close()


if __name__ == "__main__":
    unittest.main()
