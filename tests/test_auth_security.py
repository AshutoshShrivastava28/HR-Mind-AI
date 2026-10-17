import shutil
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
from app.services.authentication import (
    auth_connection,
    create_personal_document,
    create_user,
    init_auth_db,
)
from app.services.conversations import (
    create_conversation,
    init_conversation_db,
    list_conversations,
    save_message,
)


class AuthSecurityTests(unittest.TestCase):
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

        # Never contact Pinecone during tests.
        fake_vectorstore = types.ModuleType("app.rag.vectorstore")
        fake_vectorstore.deleted = []

        def fake_delete_vectors(ids, namespace=None):
            fake_vectorstore.deleted.append((list(ids), namespace))

        fake_vectorstore.delete_vectors = fake_delete_vectors
        self.previous_vectorstore = sys.modules.get("app.rag.vectorstore")
        sys.modules["app.rag.vectorstore"] = fake_vectorstore
        self.fake_vectorstore = fake_vectorstore

        app = FastAPI()
        app.include_router(auth_router)

        self.admin_client = TestClient(app, base_url="http://localhost")
        self.employee_client = TestClient(app, base_url="http://localhost")

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

        self.login(
            self.admin_client,
            self.admin,
            "AdminPassword!2026",
        )
        self.login(
            self.employee_client,
            self.employee,
            "EmployeePassword!2026",
        )

    def tearDown(self):
        self.admin_client.close()
        self.employee_client.close()

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

    def login(self, client, user, password):
        response = client.post(
            "/api/auth/login",
            json={
                "email": user["email"],
                "password": password,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_admin_can_list_users(self):
        response = self.admin_client.get("/api/auth/admin/users")
        self.assertEqual(response.status_code, 200, response.text)

        emails = {user["email"] for user in response.json()["users"]}
        self.assertIn(self.admin["email"], emails)
        self.assertIn(self.employee["email"], emails)

    def test_employee_cannot_manage_users(self):
        response = self.employee_client.get("/api/auth/admin/users")
        self.assertEqual(response.status_code, 403)

        response = self.employee_client.patch(
            f"/api/auth/admin/users/{self.employee['id']}/role",
            json={"role": "admin"},
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_promote_and_demote_verified_user(self):
        response = self.admin_client.patch(
            f"/api/auth/admin/users/{self.employee['id']}/role",
            json={"role": "admin"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["role"], "admin")

        response = self.admin_client.patch(
            f"/api/auth/admin/users/{self.employee['id']}/role",
            json={"role": "employee"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["role"], "employee")

    def test_admin_cannot_demote_self(self):
        response = self.admin_client.patch(
            f"/api/auth/admin/users/{self.admin['id']}/role",
            json={"role": "employee"},
        )
        self.assertEqual(response.status_code, 400, response.text)

        with auth_connection() as db:
            row = db.execute(
                "SELECT role FROM users WHERE id = ?",
                (self.admin["id"],),
            ).fetchone()

        self.assertEqual(row["role"], "admin")

    def test_public_registration_cannot_assign_admin_role(self):
        response = self.admin_client.post(
            "/api/auth/register",
            json={
                "display_name": "Untrusted Signup",
                "email": "self-promote@example.test",
                "password": "SecurePassword!2026",
                "role": "admin",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)

        with auth_connection() as db:
            row = db.execute(
                "SELECT role, is_verified FROM users WHERE email = ?",
                ("self-promote@example.test",),
            ).fetchone()

        self.assertEqual(row["role"], "employee")
        self.assertEqual(row["is_verified"], 0)

    def test_admin_cannot_delete_self(self):
        response = self.admin_client.delete(f"/api/auth/admin/users/{self.admin['id']}")
        self.assertEqual(response.status_code, 400, response.text)

        with auth_connection() as db:
            row = db.execute(
                "SELECT id FROM users WHERE id = ?",
                (self.admin["id"],),
            ).fetchone()

        self.assertIsNotNone(row)

    def test_admin_can_delete_employee_and_revoke_session(self):
        settings = get_settings()

        private_dir = (
            Path(settings.upload_dir) / "users" / self.employee["id"] / "doc-test"
        )
        private_dir.mkdir(parents=True, exist_ok=True)

        private_file = private_dir / "private.txt"
        private_file.write_text(
            "private user test data",
            encoding="utf-8",
        )

        create_personal_document(
            "doc-test",
            self.employee["id"],
            "private.txt",
            str(private_file),
            f"user-{self.employee['id']}",
            ["private-vector-1"],
            private_file.stat().st_size,
        )

        conversation = create_conversation(self.employee["id"])
        save_message(
            self.employee["id"],
            conversation["id"],
            "user",
            "disposable conversation content",
        )

        response = self.admin_client.delete(
            f"/api/auth/admin/users/{self.employee['id']}"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["deleted"])

        with auth_connection() as db:
            user = db.execute(
                "SELECT id FROM users WHERE id = ?",
                (self.employee["id"],),
            ).fetchone()

            docs = db.execute(
                "SELECT id FROM personal_documents WHERE user_id = ?",
                (self.employee["id"],),
            ).fetchall()

        self.assertIsNone(user)
        self.assertEqual(docs, [])
        self.assertFalse(
            (Path(settings.upload_dir) / "users" / self.employee["id"]).exists()
        )

        self.assertEqual(
            self.fake_vectorstore.deleted,
            [(["private-vector-1"], f"user-{self.employee['id']}")],
        )
        self.assertEqual(list_conversations(self.employee["id"]), [])

        # A deleted account's existing cookie must no longer authenticate.
        response = self.employee_client.get("/api/auth/me")
        self.assertEqual(response.status_code, 401, response.text)

        # The remaining administrator must still work.
        response = self.admin_client.get("/api/auth/admin/users")
        self.assertEqual(response.status_code, 200, response.text)

    def test_login_is_rate_limited(self):
        statuses = []

        for _ in range(9):
            response = self.admin_client.post(
                "/api/auth/login",
                json={
                    "email": "rate-limit-target@example.test",
                    "password": "DefinitelyWrongPassword!",
                },
            )
            statuses.append(response.status_code)

        self.assertEqual(statuses[:8], [401] * 8)
        self.assertEqual(statuses[8], 429)

    def test_private_file_cleanup_failure_preserves_account(self):
        settings = get_settings()

        private_dir = (
            Path(settings.upload_dir) / "users" / self.employee["id"] / "doc-failure"
        )
        private_dir.mkdir(parents=True, exist_ok=True)

        private_file = private_dir / "private.txt"
        private_file.write_text("private test data", encoding="utf-8")

        create_personal_document(
            "doc-failure",
            self.employee["id"],
            "private.txt",
            str(private_file),
            f"user-{self.employee['id']}",
            [],
            private_file.stat().st_size,
        )

        with patch.object(
            shutil,
            "rmtree",
            side_effect=PermissionError("simulated permission failure"),
        ):
            response = self.admin_client.delete(
                f"/api/auth/admin/users/{self.employee['id']}"
            )

        self.assertEqual(response.status_code, 503, response.text)

        with auth_connection() as db:
            user = db.execute(
                "SELECT id FROM users WHERE id = ?",
                (self.employee["id"],),
            ).fetchone()

        self.assertIsNotNone(user)
        self.assertTrue(private_file.exists())


if __name__ == "__main__":
    unittest.main()
