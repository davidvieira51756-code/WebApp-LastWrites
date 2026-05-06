from __future__ import annotations

import os
import secrets
import unittest
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

os.environ.setdefault("LOCAL_DEV_MODE", "true")

import backend.main as backend_main
from backend.services.file_crypto_service import (
    b64url_encode,
    public_jwk_from_rsa_public_key,
    sha256_hexdigest,
)
from cryptography.hazmat.primitives.asymmetric import rsa


class FakeCosmosService:
    def __init__(self) -> None:
        self.updated_vault_id: str | None = None
        self.updated_payload: dict[str, Any] | None = None

    def update_vault(self, vault_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.updated_vault_id = vault_id
        self.updated_payload = dict(payload)
        merged = {"id": vault_id}
        merged.update(payload)
        return merged


class RecordingVaultKeyService:
    def __init__(self, key_metadata: dict[str, Any] | None = None) -> None:
        self.key_metadata = key_metadata or {}
        self.ensure_calls: list[str] = []
        self.unwrap_calls: list[tuple[str, str]] = []
        self.unwrap_result = b""

    def ensure_vault_key(self, vault_id: str) -> dict[str, Any]:
        self.ensure_calls.append(vault_id)
        return dict(self.key_metadata)

    def unwrap_file_key(self, *, key_kid: str, wrapped_key: str) -> bytes:
        self.unwrap_calls.append((key_kid, wrapped_key))
        return self.unwrap_result


class FakeBlobService:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.download_calls: list[tuple[str, str]] = []

    def download_blob_bytes(self, *, container_name: str, blob_name: str) -> bytes:
        self.download_calls.append((container_name, blob_name))
        return self.payload


class BackendSecurityHelperTests(unittest.TestCase):
    def test_ensure_vault_key_metadata_keeps_existing_modern_key(self) -> None:
        public_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
        vault_item = {
            "id": "vault-123",
            "key_kid": "local://vault-keys/vault-key-vault-123/versions/v1",
            "public_jwk": public_jwk_from_rsa_public_key(public_key),
            "key_size_bits": 4096,
        }
        cosmos_service = FakeCosmosService()
        key_service = RecordingVaultKeyService()

        result = backend_main.ensure_vault_key_metadata(cosmos_service, key_service, vault_item)

        self.assertIs(result, vault_item)
        self.assertEqual(key_service.ensure_calls, [])
        self.assertIsNone(cosmos_service.updated_vault_id)

    def test_ensure_vault_key_metadata_rotates_legacy_key(self) -> None:
        legacy_public_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
        vault_item = {
            "id": "vault-legacy",
            "key_kid": "legacy-key",
            "public_jwk": public_jwk_from_rsa_public_key(legacy_public_key),
            "key_size_bits": 2048,
        }
        key_metadata = {
            "key_kid": "local://vault-keys/vault-key-vault-legacy/versions/v2",
            "key_version": "v2",
            "key_size_bits": 4096,
            "public_jwk": {"kty": "RSA", "n": "abc", "e": "AQAB"},
        }
        cosmos_service = FakeCosmosService()
        key_service = RecordingVaultKeyService(key_metadata=key_metadata)

        result = backend_main.ensure_vault_key_metadata(cosmos_service, key_service, vault_item)

        self.assertEqual(key_service.ensure_calls, ["vault-legacy"])
        self.assertEqual(cosmos_service.updated_vault_id, "vault-legacy")
        self.assertEqual(result["key_kid"], key_metadata["key_kid"])
        self.assertEqual(result["key_size_bits"], 4096)

    def test_normalize_file_recipient_emails_defaults_to_all_known_recipients(self) -> None:
        recipients = [
            {"email": "alice@example.com", "can_activate": True},
            {"email": "bob@example.com", "can_activate": False},
        ]

        normalized = backend_main.normalize_file_recipient_emails(None, recipients)

        self.assertEqual(normalized, ["alice@example.com", "bob@example.com"])

    def test_normalize_file_recipient_emails_rejects_unknown_recipient(self) -> None:
        recipients = [{"email": "alice@example.com", "can_activate": True}]

        with self.assertRaisesRegex(ValueError, "Unknown recipient assignment: bob@example.com"):
            backend_main.normalize_file_recipient_emails(["bob@example.com"], recipients)

    def test_download_vault_file_bytes_decrypts_and_verifies_integrity(self) -> None:
        plaintext = b"confidential document"
        aes_key = secrets.token_bytes(32)
        iv = secrets.token_bytes(12)
        ciphertext_with_tag = AESGCM(aes_key).encrypt(iv, plaintext, None)
        ciphertext = ciphertext_with_tag[:-16]
        tag = ciphertext_with_tag[-16:]

        blob_service = FakeBlobService(ciphertext)
        key_service = RecordingVaultKeyService()
        key_service.unwrap_result = aes_key

        result = backend_main._download_vault_file_bytes(
            blob_service=blob_service,
            vault_key_service=key_service,
            file_metadata={
                "container_name": "vault-files",
                "blob_name": "cipher.bin",
                "encrypted": True,
                "wrapped_key": "wrapped-key-placeholder",
                "iv": b64url_encode(iv),
                "tag": b64url_encode(tag),
                "key_kid": "local://vault-keys/key/versions/v1",
                "plaintext_sha256": sha256_hexdigest(plaintext),
            },
        )

        self.assertEqual(result, plaintext)
        self.assertEqual(blob_service.download_calls, [("vault-files", "cipher.bin")])
        self.assertEqual(
            key_service.unwrap_calls,
            [("local://vault-keys/key/versions/v1", "wrapped-key-placeholder")],
        )

    def test_download_vault_file_bytes_rejects_integrity_mismatch(self) -> None:
        plaintext = b"confidential document"
        aes_key = secrets.token_bytes(32)
        iv = secrets.token_bytes(12)
        ciphertext_with_tag = AESGCM(aes_key).encrypt(iv, plaintext, None)
        ciphertext = ciphertext_with_tag[:-16]
        tag = ciphertext_with_tag[-16:]

        blob_service = FakeBlobService(ciphertext)
        key_service = RecordingVaultKeyService()
        key_service.unwrap_result = aes_key

        with self.assertRaisesRegex(ValueError, "File integrity verification failed after decryption."):
            backend_main._download_vault_file_bytes(
                blob_service=blob_service,
                vault_key_service=key_service,
                file_metadata={
                    "container_name": "vault-files",
                    "blob_name": "cipher.bin",
                    "encrypted": True,
                    "wrapped_key": "wrapped-key-placeholder",
                    "iv": b64url_encode(iv),
                    "tag": b64url_encode(tag),
                    "key_kid": "local://vault-keys/key/versions/v1",
                    "plaintext_sha256": sha256_hexdigest(b"different payload"),
                },
            )


if __name__ == "__main__":
    unittest.main()
