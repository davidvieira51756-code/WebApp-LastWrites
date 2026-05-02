from __future__ import annotations

import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
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
            json={
                "email": email,
                "username": f"user_{uuid4().hex[:8]}",
                "full_name": "Integration Test User",
                "birth_date": "2000-01-01",
                "password": password,
            },
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

    def _create_vault(
        self,
        token: str,
        *,
        name: str,
        owner_message: str,
        grace_period_value: int = 7,
        grace_period_unit: str = "days",
    ) -> dict:
        response = self.client.post(
            "/vaults",
            headers=self._auth_headers(token),
            json={
                "name": name,
                "owner_message": owner_message,
                "grace_period_value": grace_period_value,
                "grace_period_unit": grace_period_unit,
                "recipients": [],
                "activation_threshold": 1,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _upload_file(self, token: str, vault_id: str, file_name: str, content: bytes) -> dict:
        return self._upload_file_for_recipients(
            token,
            vault_id,
            file_name,
            content,
            recipient_emails=None,
        )

    def _upload_file_for_recipients(
        self,
        token: str,
        vault_id: str,
        file_name: str,
        content: bytes,
        *,
        recipient_emails: list[str] | None,
    ) -> dict:
        data = {}
        if recipient_emails is not None:
            data["recipient_emails_json"] = json.dumps(recipient_emails)
        response = self.client.post(
            f"/vaults/{vault_id}/files",
            headers=self._auth_headers(token),
            files={"file": (file_name, content, "text/plain")},
            data=data,
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["file"]

    def _deliver_vault(self, vault_id: str) -> dict:
        internal_vault_id = self.backend_main.app.state.cosmos_service.get_vault_by_short_id(vault_id)["id"]
        updated_vault = self.backend_main.app.state.cosmos_service.update_vault(
            internal_vault_id,
            {"status": "delivery_initiated"},
        )
        self.assertIsNotNone(updated_vault)

        os.environ["VAULT_ID"] = internal_vault_id
        os.environ["DELIVERIES_CONTAINER"] = "deliveries"
        sys.modules.pop("worker_container.app", None)
        worker_app = importlib.import_module("worker_container.app")

        exit_code = worker_app.run()
        self.assertEqual(exit_code, 0)
        return {"internal_vault_id": internal_vault_id}

    def test_encrypted_upload_and_owner_download_round_trip(self) -> None:
        email = f"owner-{uuid4().hex[:8]}@example.com"
        token = self._register_and_login(email)

        vault = self._create_vault(
            token,
            name="Encrypted Round Trip",
            owner_message="This should appear on the delivery cover.",
        )
        self.assertEqual(vault["owner_message"], "This should appear on the delivery cover.")
        self.assertRegex(vault["id"], r"^[a-z0-9]{8}$")
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

    def test_worker_generates_recipient_specific_delivery_zips_for_encrypted_vault(self) -> None:
        email = f"worker-owner-{uuid4().hex[:8]}@example.com"
        token = self._register_and_login(email)

        vault = self._create_vault(
            token,
            name="Worker Delivery Vault",
            owner_message="Final instructions for the recipients.",
        )
        vault_id = vault["id"]
        first_recipient_email = f"recipient-a-{uuid4().hex[:8]}@example.com"
        second_recipient_email = f"recipient-b-{uuid4().hex[:8]}@example.com"

        add_first_recipient_response = self.client.post(
            f"/vaults/{vault_id}/recipients",
            headers=self._auth_headers(token),
            json={"email": first_recipient_email, "can_activate": True},
        )
        self.assertEqual(add_first_recipient_response.status_code, 200, add_first_recipient_response.text)
        add_second_recipient_response = self.client.post(
            f"/vaults/{vault_id}/recipients",
            headers=self._auth_headers(token),
            json={"email": second_recipient_email, "can_activate": True},
        )
        self.assertEqual(add_second_recipient_response.status_code, 200, add_second_recipient_response.text)

        first_file = self._upload_file_for_recipients(
            token,
            vault_id,
            "letter.txt",
            b"first encrypted file",
            recipient_emails=[first_recipient_email],
        )
        second_file = self._upload_file_for_recipients(
            token,
            vault_id,
            "notes.txt",
            b"second encrypted file",
            recipient_emails=[second_recipient_email],
        )
        shared_file = self._upload_file_for_recipients(
            token,
            vault_id,
            "shared.txt",
            b"shared encrypted file",
            recipient_emails=[first_recipient_email, second_recipient_email],
        )

        self._deliver_vault(vault_id)

        vault_response = self.client.get(
            f"/vaults/{vault_id}",
            headers=self._auth_headers(token),
        )
        self.assertEqual(vault_response.status_code, 200, vault_response.text)
        delivered_vault = vault_response.json()
        self.assertEqual(delivered_vault["status"], "delivered_archived")
        self.assertEqual(delivered_vault["delivery_container_name"], "deliveries")
        self.assertTrue(delivered_vault["delivery_blob_name"])
        self.assertTrue(delivered_vault["delivered_at"])
        self.assertEqual(len(delivered_vault["delivery_packages"]), 2)

        owner_first_package_response = self.client.get(
            f"/vaults/{vault_id}/delivery-package",
            headers=self._auth_headers(token),
            params={"recipient_email": first_recipient_email},
        )
        self.assertEqual(owner_first_package_response.status_code, 200, owner_first_package_response.text)
        self.assertEqual(owner_first_package_response.headers.get("content-type"), "application/zip")
        self.assertIn(vault_id, owner_first_package_response.headers.get("content-disposition", ""))

        first_archive = ZipFile(io.BytesIO(owner_first_package_response.content))
        first_archive_names = set(first_archive.namelist())
        self.assertIn("Delivery.pdf", first_archive_names)
        self.assertIn(first_file["file_name"], first_archive_names)
        self.assertIn(shared_file["file_name"], first_archive_names)
        self.assertNotIn(second_file["file_name"], first_archive_names)

        recipient_one_token = self._register_and_login(first_recipient_email)
        recipient_one_package_response = self.client.get(
            f"/vaults/{vault_id}/delivery-package",
            headers=self._auth_headers(recipient_one_token),
        )
        self.assertEqual(recipient_one_package_response.status_code, 200, recipient_one_package_response.text)
        recipient_one_archive = ZipFile(io.BytesIO(recipient_one_package_response.content))
        self.assertEqual(set(recipient_one_archive.namelist()), first_archive_names)

        recipient_two_token = self._register_and_login(second_recipient_email)
        recipient_two_package_response = self.client.get(
            f"/vaults/{vault_id}/delivery-package",
            headers=self._auth_headers(recipient_two_token),
        )
        self.assertEqual(recipient_two_package_response.status_code, 200, recipient_two_package_response.text)
        second_archive = ZipFile(io.BytesIO(recipient_two_package_response.content))
        second_archive_names = set(second_archive.namelist())
        self.assertIn("Delivery.pdf", second_archive_names)
        self.assertIn(second_file["file_name"], second_archive_names)
        self.assertIn(shared_file["file_name"], second_archive_names)
        self.assertNotIn(first_file["file_name"], second_archive_names)

    def test_profile_update_and_short_id_public_access(self) -> None:
        email = f"profile-owner-{uuid4().hex[:8]}@example.com"
        token = self._register_and_login(email)

        me_response = self.client.get("/auth/me", headers=self._auth_headers(token))
        self.assertEqual(me_response.status_code, 200, me_response.text)
        self.assertEqual(me_response.json()["display_name_preference"], "username")
        self.assertEqual(me_response.json()["account_status"], "active")

        update_response = self.client.patch(
            "/auth/me",
            headers=self._auth_headers(token),
            json={
                "username": "owner_public",
                "full_name": "Owner Public Name",
                "birth_date": "1995-05-05",
                "display_name_preference": "real_name",
            },
        )
        self.assertEqual(update_response.status_code, 200, update_response.text)
        self.assertEqual(update_response.json()["full_name"], "Owner Public Name")

        underage_response = self.client.post(
            "/auth/register",
            json={
                "email": f"underage-{uuid4().hex[:8]}@example.com",
                "username": f"teen_{uuid4().hex[:6]}",
                "full_name": "Too Young",
                "birth_date": "2018-01-01",
                "password": "Password123!",
            },
        )
        self.assertEqual(underage_response.status_code, 400, underage_response.text)

        vault = self._create_vault(
            token,
            name="Public Id Vault",
            owner_message="Public identifier test.",
        )
        self.assertRegex(vault["id"], r"^[a-z0-9]{8}$")

        recipient_email = f"recipient-{uuid4().hex[:8]}@example.com"
        add_recipient_response = self.client.post(
            f"/vaults/{vault['id']}/recipients",
            headers=self._auth_headers(token),
            json={"email": recipient_email},
        )
        self.assertEqual(add_recipient_response.status_code, 200, add_recipient_response.text)

        recipient_token = self._register_and_login(recipient_email)
        incoming_response = self.client.get(
            "/vaults/incoming",
            headers=self._auth_headers(recipient_token),
        )
        self.assertEqual(incoming_response.status_code, 200, incoming_response.text)
        incoming_payload = incoming_response.json()
        self.assertEqual(incoming_payload[0]["id"], vault["id"])
        self.assertEqual(incoming_payload[0]["owner_display_name"], "Owner Public Name")
        self.assertEqual(incoming_payload[0]["owner_username"], "owner_public")

    def test_recipient_activation_permissions_limit_threshold(self) -> None:
        email = f"permission-owner-{uuid4().hex[:8]}@example.com"
        token = self._register_and_login(email)

        vault = self._create_vault(
            token,
            name="Recipient Permissions Vault",
            owner_message="Permission checks.",
        )
        vault_id = vault["id"]
        first_recipient_email = f"can-activate-{uuid4().hex[:8]}@example.com"
        second_recipient_email = f"cannot-activate-{uuid4().hex[:8]}@example.com"

        add_first_recipient_response = self.client.post(
            f"/vaults/{vault_id}/recipients",
            headers=self._auth_headers(token),
            json={"email": first_recipient_email, "can_activate": True},
        )
        self.assertEqual(add_first_recipient_response.status_code, 200, add_first_recipient_response.text)
        add_second_recipient_response = self.client.post(
            f"/vaults/{vault_id}/recipients",
            headers=self._auth_headers(token),
            json={"email": second_recipient_email, "can_activate": True},
        )
        self.assertEqual(add_second_recipient_response.status_code, 200, add_second_recipient_response.text)

        threshold_update_response = self.client.patch(
            f"/vaults/{vault_id}",
            headers=self._auth_headers(token),
            json={"activation_threshold": 2},
        )
        self.assertEqual(threshold_update_response.status_code, 200, threshold_update_response.text)
        self.assertEqual(threshold_update_response.json()["activation_threshold"], 2)

        permission_update_response = self.client.patch(
            f"/vaults/{vault_id}/recipients/{second_recipient_email}",
            headers=self._auth_headers(token),
            json={"can_activate": False},
        )
        self.assertEqual(permission_update_response.status_code, 200, permission_update_response.text)
        self.assertEqual(permission_update_response.json()["activation_threshold"], 1)

        updated_vault_response = self.client.get(
            f"/vaults/{vault_id}",
            headers=self._auth_headers(token),
        )
        self.assertEqual(updated_vault_response.status_code, 200, updated_vault_response.text)
        updated_vault = updated_vault_response.json()
        self.assertEqual(updated_vault["activation_threshold"], 1)
        self.assertEqual(
            {recipient["email"]: recipient["can_activate"] for recipient in updated_vault["recipients"]},
            {
                first_recipient_email: True,
                second_recipient_email: False,
            },
        )

        blocked_recipient_token = self._register_and_login(second_recipient_email)
        blocked_activation_response = self.client.post(
            f"/vaults/{vault_id}/activation-requests",
            headers=self._auth_headers(blocked_recipient_token),
            json={"reason": "I should not be allowed to activate."},
        )
        self.assertEqual(blocked_activation_response.status_code, 403, blocked_activation_response.text)
        self.assertEqual(
            blocked_activation_response.json()["detail"],
            "This recipient is not allowed to activate the vault.",
        )

        allowed_recipient_token = self._register_and_login(first_recipient_email)
        allowed_activation_response = self.client.post(
            f"/vaults/{vault_id}/activation-requests",
            headers=self._auth_headers(allowed_recipient_token),
            json={"reason": "I can activate this vault."},
        )
        self.assertEqual(allowed_activation_response.status_code, 201, allowed_activation_response.text)
        self.assertEqual(allowed_activation_response.json()["activation_threshold"], 1)
        self.assertTrue(allowed_activation_response.json()["can_activate"])

    def test_grace_period_supports_hours(self) -> None:
        email = f"hours-owner-{uuid4().hex[:8]}@example.com"
        token = self._register_and_login(email)

        vault = self._create_vault(
            token,
            name="Hours Vault",
            owner_message="Hours-based grace period.",
            grace_period_value=12,
            grace_period_unit="hours",
        )
        self.assertEqual(vault["grace_period_value"], 12)
        self.assertEqual(vault["grace_period_unit"], "hours")

        recipient_email = f"hours-recipient-{uuid4().hex[:8]}@example.com"
        add_recipient_response = self.client.post(
            f"/vaults/{vault['id']}/recipients",
            headers=self._auth_headers(token),
            json={"email": recipient_email, "can_activate": True},
        )
        self.assertEqual(add_recipient_response.status_code, 200, add_recipient_response.text)

        recipient_token = self._register_and_login(recipient_email)
        activation_response = self.client.post(
            f"/vaults/{vault['id']}/activation-requests",
            headers=self._auth_headers(recipient_token),
            json={"reason": "Checking hour-based grace periods."},
        )
        self.assertEqual(activation_response.status_code, 201, activation_response.text)
        self.assertEqual(activation_response.json()["grace_period_value"], 12)
        self.assertEqual(activation_response.json()["grace_period_unit"], "hours")

        vault_response = self.client.get(
            f"/vaults/{vault['id']}",
            headers=self._auth_headers(token),
        )
        self.assertEqual(vault_response.status_code, 200, vault_response.text)
        updated_vault = vault_response.json()
        self.assertEqual(updated_vault["status"], "grace_period")
        started_at = datetime.fromisoformat(updated_vault["grace_period_started_at"])
        expires_at = datetime.fromisoformat(updated_vault["grace_period_expires_at"])
        self.assertAlmostEqual((expires_at - started_at).total_seconds(), 12 * 3600, delta=5)

    def test_account_deletion_is_blocked_for_active_vaults(self) -> None:
        email = f"delete-block-owner-{uuid4().hex[:8]}@example.com"
        password = "Password123!"
        token = self._register_and_login(email, password=password)

        self._create_vault(
            token,
            name="Blocking Vault",
            owner_message="Account deletion should be blocked.",
        )

        delete_response = self.client.request(
            "DELETE",
            "/auth/me",
            headers=self._auth_headers(token),
            json={"password": password},
        )
        self.assertEqual(delete_response.status_code, 409, delete_response.text)
        self.assertEqual(
            delete_response.json()["detail"],
            "This account cannot be deleted while it still owns vaults that are active or pending delivery.",
        )

    def test_delivered_archived_vault_blocks_mutations(self) -> None:
        email = f"archived-owner-{uuid4().hex[:8]}@example.com"
        token = self._register_and_login(email)

        vault = self._create_vault(
            token,
            name="Archived Vault",
            owner_message="Archive mutation lock test.",
        )
        vault_id = vault["id"]

        add_recipient_response = self.client.post(
            f"/vaults/{vault_id}/recipients",
            headers=self._auth_headers(token),
            json={"email": f"recipient-{uuid4().hex[:8]}@example.com"},
        )
        self.assertEqual(add_recipient_response.status_code, 200, add_recipient_response.text)

        uploaded_file = self._upload_file(token, vault_id, "archive.txt", b"archived content")
        self._deliver_vault(vault_id)

        expected_error = "This vault has already been delivered and archived. It can no longer be modified."

        update_response = self.client.patch(
            f"/vaults/{vault_id}",
            headers=self._auth_headers(token),
            json={"name": "Should Not Change"},
        )
        self.assertEqual(update_response.status_code, 409, update_response.text)
        self.assertEqual(update_response.json()["detail"], expected_error)

        add_recipient_again_response = self.client.post(
            f"/vaults/{vault_id}/recipients",
            headers=self._auth_headers(token),
            json={"email": f"second-{uuid4().hex[:8]}@example.com"},
        )
        self.assertEqual(add_recipient_again_response.status_code, 409, add_recipient_again_response.text)
        self.assertEqual(add_recipient_again_response.json()["detail"], expected_error)

        remove_recipient_response = self.client.delete(
            f"/vaults/{vault_id}/recipients/{add_recipient_response.json()['recipients'][0]['email']}",
            headers=self._auth_headers(token),
        )
        self.assertEqual(remove_recipient_response.status_code, 409, remove_recipient_response.text)
        self.assertEqual(remove_recipient_response.json()["detail"], expected_error)

        upload_after_archive_response = self.client.post(
            f"/vaults/{vault_id}/files",
            headers=self._auth_headers(token),
            files={"file": ("blocked.txt", b"blocked content", "text/plain")},
        )
        self.assertEqual(upload_after_archive_response.status_code, 409, upload_after_archive_response.text)
        self.assertEqual(upload_after_archive_response.json()["detail"], expected_error)

        delete_file_response = self.client.delete(
            f"/vaults/{vault_id}/files/{uploaded_file['id']}",
            headers=self._auth_headers(token),
        )
        self.assertEqual(delete_file_response.status_code, 409, delete_file_response.text)
        self.assertEqual(delete_file_response.json()["detail"], expected_error)


if __name__ == "__main__":
    unittest.main()
