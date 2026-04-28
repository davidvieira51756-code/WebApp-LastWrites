from __future__ import annotations

import importlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fastapi.testclient import TestClient


class EncryptedDeliveryFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temp_dir = tempfile.TemporaryDirectory(prefix="lastwrites-tests-")
        cls._temp_root = Path(cls._temp_dir.name)

        os.environ["LOCAL_DEV_MODE"] = "true"
        os.environ["LOCAL_COSMOS_DATA_FILE"] = str(cls._temp_root / "vaults.json")
        os.environ["LOCAL_BLOB_ROOT_DIR"] = str(cls._temp_root / "blobs")
        os.environ["LOCAL_VAULT_KEYS_DIR"] = str(cls._temp_root / "vault_keys")
        os.environ["AUTH_SECRET_KEY"] = "integration-test-secret-key"
        os.environ["FRONTEND_VERIFY_EMAIL_URL"] = "http://localhost:3000/verify-email"
        os.environ["AUTH_EXPOSE_VERIFICATION_TOKEN"] = "true"
        os.environ["AUTH_REQUIRE_EMAIL_VERIFICATION"] = "true"

        sys.modules.pop("backend.main", None)
        cls.backend_main = importlib.import_module("backend.main")
        cls.client = TestClient(cls.backend_main.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)
        cls._temp_dir.cleanup()

    def _register_and_login(self, email: str, password: str = "Password123!") -> str:
        register_response = self.client.post(
            "/auth/register",
            json={"email": email, "password": password},
        )
        self.assertEqual(register_response.status_code, 201, register_response.text)
        verification_token = register_response.json().get("verification_token")
        self.assertTrue(verification_token)

        verify_response = self.client.post(
            "/auth/verify-email",
            json={"token": verification_token},
        )
        self.assertEqual(verify_response.status_code, 200, verify_response.text)

        login_response = self.client.post(
            "/auth/login",
            json={"email": email, "password": password},
        )
        self.assertEqual(login_response.status_code, 200, login_response.text)
        return login_response.json()["access_token"]

    @staticmethod
    def _auth_headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _create_vault(self, token: str, *, name: str, owner_message: str) -> dict:
        response = self.client.post(
            "/vaults",
            headers=self._auth_headers(token),
            json={
                "name": name,
                "owner_message": owner_message,
                "grace_period_days": 7,
                "recipients": [],
                "activation_threshold": 1,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _upload_file(self, token: str, vault_id: str, file_name: str, content: bytes) -> dict:
        response = self.client.post(
            f"/vaults/{vault_id}/files",
            headers=self._auth_headers(token),
            files={"file": (file_name, content, "text/plain")},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["file"]

    def test_encrypted_upload_and_owner_download_round_trip(self) -> None:
        email = f"owner-{uuid4().hex[:8]}@example.com"
        token = self._register_and_login(email)

        vault = self._create_vault(
            token,
            name="Encrypted Round Trip",
            owner_message="This should appear on the delivery cover.",
        )
        self.assertEqual(vault["owner_message"], "This should appear on the delivery cover.")
        self.assertTrue(str(vault["key_kid"]).startswith("local://"))
        self.assertIn("public_jwk", vault)

        plaintext = b"hello from the encrypted upload pipeline"
        uploaded_file = self._upload_file(token, vault["id"], "hello.txt", plaintext)

        self.assertTrue(uploaded_file["encrypted"])
        self.assertEqual(uploaded_file["algorithm"], "AES-256-GCM")
        self.assertTrue(uploaded_file["wrapped_key"])
        self.assertTrue(uploaded_file["iv"])
        self.assertTrue(uploaded_file["tag"])

        ciphertext_path = (
            Path(os.environ["LOCAL_BLOB_ROOT_DIR"])
            / uploaded_file["container_name"]
            / uploaded_file["blob_name"]
        )
        self.assertTrue(ciphertext_path.exists())
        self.assertNotEqual(ciphertext_path.read_bytes(), plaintext)

        download_response = self.client.get(
            f"/vaults/{vault['id']}/files/{uploaded_file['id']}/download",
            headers=self._auth_headers(token),
        )
        self.assertEqual(download_response.status_code, 200, download_response.text)
        self.assertEqual(download_response.content, plaintext)
        self.assertIn("attachment", download_response.headers.get("content-disposition", ""))

    def test_worker_generates_delivery_zip_for_encrypted_vault(self) -> None:
        email = f"worker-owner-{uuid4().hex[:8]}@example.com"
        token = self._register_and_login(email)
        recipient_email = f"recipient-{uuid4().hex[:8]}@example.com"

        vault = self._create_vault(
            token,
            name="Worker Delivery Vault",
            owner_message="Final instructions for the recipients.",
        )
        vault_id = vault["id"]

        add_recipient_response = self.client.post(
            f"/vaults/{vault_id}/recipients",
            headers=self._auth_headers(token),
            json={"email": recipient_email},
        )
        self.assertEqual(add_recipient_response.status_code, 200, add_recipient_response.text)

        first_file = self._upload_file(token, vault_id, "letter.txt", b"first encrypted file")
        second_file = self._upload_file(token, vault_id, "notes.txt", b"second encrypted file")

        updated_vault = self.backend_main.app.state.cosmos_service.update_vault(
            vault_id,
            {"status": "delivery_initiated"},
        )
        self.assertIsNotNone(updated_vault)

        os.environ["VAULT_ID"] = vault_id
        os.environ["DELIVERIES_CONTAINER"] = "deliveries"
        sys.modules.pop("worker_container.app", None)
        worker_app = importlib.import_module("worker_container.app")

        exit_code = worker_app.run()
        self.assertEqual(exit_code, 0)

        vault_response = self.client.get(
            f"/vaults/{vault_id}",
            headers=self._auth_headers(token),
        )
        self.assertEqual(vault_response.status_code, 200, vault_response.text)
        delivered_vault = vault_response.json()
        self.assertEqual(delivered_vault["status"], "delivered")
        self.assertEqual(delivered_vault["delivery_container_name"], "deliveries")
        self.assertTrue(delivered_vault["delivery_blob_name"])
        self.assertTrue(delivered_vault["delivered_at"])

        package_response = self.client.get(
            f"/vaults/{vault_id}/delivery-package",
            headers=self._auth_headers(token),
        )
        self.assertEqual(package_response.status_code, 200, package_response.text)
        self.assertEqual(package_response.headers.get("content-type"), "application/zip")

        package_link_response = self.client.get(
            f"/vaults/{vault_id}/delivery-package-link",
            headers=self._auth_headers(token),
        )
        self.assertEqual(package_link_response.status_code, 200, package_link_response.text)
        package_link_payload = package_link_response.json()
        self.assertIn("/local-downloads/deliveries/", package_link_payload["download_url"])
        self.assertTrue(package_link_payload["expires_at"])

        audit_response = self.client.get(
            f"/vaults/{vault_id}/audit",
            headers=self._auth_headers(token),
        )
        self.assertEqual(audit_response.status_code, 200, audit_response.text)
        audit_event_types = [event["event_type"] for event in audit_response.json()]
        self.assertIn("login", audit_event_types)
        self.assertIn("vault_created", audit_event_types)
        self.assertIn("delivery_completed", audit_event_types)

        archive = ZipFile(io.BytesIO(package_response.content))
        archive_names = set(archive.namelist())
        self.assertIn("Delivery.pdf", archive_names)
        self.assertNotIn("00-cover.pdf", archive_names)
        self.assertIn(first_file["file_name"], archive_names)
        self.assertIn(second_file["file_name"], archive_names)

    def test_activation_request_creates_audit_events(self) -> None:
        owner_email = f"owner-audit-{uuid4().hex[:8]}@example.com"
        recipient_email = f"recipient-audit-{uuid4().hex[:8]}@example.com"
        owner_token = self._register_and_login(owner_email)
        recipient_token = self._register_and_login(recipient_email)

        vault = self._create_vault(
            owner_token,
            name="Audit Vault",
            owner_message="Audit trail check",
        )
        vault_id = vault["id"]

        add_recipient_response = self.client.post(
            f"/vaults/{vault_id}/recipients",
            headers=self._auth_headers(owner_token),
            json={"email": recipient_email},
        )
        self.assertEqual(add_recipient_response.status_code, 200, add_recipient_response.text)

        activation_response = self.client.post(
            f"/vaults/{vault_id}/activation-requests",
            headers=self._auth_headers(recipient_token),
            json={"reason": "Threshold should start grace period."},
        )
        self.assertEqual(activation_response.status_code, 201, activation_response.text)

        audit_response = self.client.get(
            f"/vaults/{vault_id}/audit",
            headers=self._auth_headers(owner_token),
        )
        self.assertEqual(audit_response.status_code, 200, audit_response.text)
        audit_event_types = [event["event_type"] for event in audit_response.json()]
        self.assertIn("activation_requested", audit_event_types)
        self.assertIn("grace_period_started", audit_event_types)


if __name__ == "__main__":
    unittest.main()
