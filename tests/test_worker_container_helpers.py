from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]


def _install_worker_dependency_stubs() -> None:
    azure_module = types.ModuleType("azure")
    core_module = types.ModuleType("azure.core")
    core_exceptions_module = types.ModuleType("azure.core.exceptions")
    cosmos_module = types.ModuleType("azure.cosmos")
    identity_module = types.ModuleType("azure.identity")
    keyvault_module = types.ModuleType("azure.keyvault")
    keys_module = types.ModuleType("azure.keyvault.keys")
    crypto_module = types.ModuleType("azure.keyvault.keys.crypto")
    storage_module = types.ModuleType("azure.storage")
    blob_module = types.ModuleType("azure.storage.blob")
    monitor_module = types.ModuleType("azure.monitor")
    opentelemetry_module = types.ModuleType("azure.monitor.opentelemetry")
    cryptography_module = types.ModuleType("cryptography")
    hazmat_module = types.ModuleType("cryptography.hazmat")
    primitives_module = types.ModuleType("cryptography.hazmat.primitives")
    hashes_module = types.ModuleType("cryptography.hazmat.primitives.hashes")
    serialization_module = types.ModuleType("cryptography.hazmat.primitives.serialization")
    asymmetric_module = types.ModuleType("cryptography.hazmat.primitives.asymmetric")
    padding_module = types.ModuleType("cryptography.hazmat.primitives.asymmetric.padding")
    ciphers_module = types.ModuleType("cryptography.hazmat.primitives.ciphers")
    aead_module = types.ModuleType("cryptography.hazmat.primitives.ciphers.aead")
    reportlab_module = types.ModuleType("reportlab")
    lib_module = types.ModuleType("reportlab.lib")
    colors_module = types.ModuleType("reportlab.lib.colors")
    pagesizes_module = types.ModuleType("reportlab.lib.pagesizes")
    styles_module = types.ModuleType("reportlab.lib.styles")
    platypus_module = types.ModuleType("reportlab.platypus")
    email_service_module = types.ModuleType("worker_container.email_service")

    class _AzureError(Exception):
        pass

    class _ResourceExistsError(Exception):
        pass

    class _CosmosHttpResponseError(Exception):
        pass

    class _CosmosClient:
        @classmethod
        def from_connection_string(cls, connection_string: str):
            raise AssertionError("CosmosClient.from_connection_string should be mocked in tests.")

    class _DefaultAzureCredential:
        pass

    class _CryptographyClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class _EncryptionAlgorithm:
        rsa_oaep_256 = "rsa-oaep-256"

    class _BlobServiceClient:
        @classmethod
        def from_connection_string(cls, connection_string: str):
            raise AssertionError("BlobServiceClient.from_connection_string should be mocked in tests.")

    class _ContentSettings:
        def __init__(self, content_type: str | None = None) -> None:
            self.content_type = content_type

    class _AESGCM:
        def __init__(self, key: bytes) -> None:
            self.key = key

        def decrypt(self, nonce: bytes, data: bytes, associated_data) -> bytes:
            return data

    class _Paragraph:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class _Spacer:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class _Table:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def setStyle(self, style) -> None:
            self.style = style

    class _TableStyle:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class _SimpleDocTemplate:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def build(self, story) -> None:
            self.story = story

    class _EmailService:
        def __init__(self, *args, **kwargs) -> None:
            pass

    core_exceptions_module.AzureError = _AzureError
    core_exceptions_module.ResourceExistsError = _ResourceExistsError
    cosmos_module.CosmosClient = _CosmosClient
    cosmos_module.exceptions = types.SimpleNamespace(
        CosmosHttpResponseError=_CosmosHttpResponseError
    )
    identity_module.DefaultAzureCredential = _DefaultAzureCredential
    crypto_module.CryptographyClient = _CryptographyClient
    crypto_module.EncryptionAlgorithm = _EncryptionAlgorithm
    blob_module.BlobServiceClient = _BlobServiceClient
    blob_module.ContentSettings = _ContentSettings
    opentelemetry_module.configure_azure_monitor = lambda **kwargs: None
    aead_module.AESGCM = _AESGCM
    pagesizes_module.A4 = (595, 842)
    styles_module.getSampleStyleSheet = lambda: {
        "Title": object(),
        "Heading2": object(),
        "Heading3": object(),
        "BodyText": object(),
    }
    colors_module.HexColor = lambda value: value
    colors_module.white = "white"
    platypus_module.Paragraph = _Paragraph
    platypus_module.SimpleDocTemplate = _SimpleDocTemplate
    platypus_module.Spacer = _Spacer
    platypus_module.Table = _Table
    platypus_module.TableStyle = _TableStyle
    email_service_module.EmailService = _EmailService

    azure_module.core = core_module
    azure_module.cosmos = cosmos_module
    azure_module.identity = identity_module
    azure_module.keyvault = keyvault_module
    azure_module.storage = storage_module
    azure_module.monitor = monitor_module
    core_module.exceptions = core_exceptions_module
    keyvault_module.keys = keys_module
    keys_module.crypto = crypto_module
    storage_module.blob = blob_module
    monitor_module.opentelemetry = opentelemetry_module
    cryptography_module.hazmat = hazmat_module
    hazmat_module.primitives = primitives_module
    primitives_module.hashes = hashes_module
    primitives_module.serialization = serialization_module
    primitives_module.asymmetric = asymmetric_module
    primitives_module.ciphers = ciphers_module
    asymmetric_module.padding = padding_module
    ciphers_module.aead = aead_module
    reportlab_module.lib = lib_module
    reportlab_module.platypus = platypus_module
    lib_module.colors = colors_module
    lib_module.pagesizes = pagesizes_module
    lib_module.styles = styles_module

    sys.modules["azure"] = azure_module
    sys.modules["azure.core"] = core_module
    sys.modules["azure.core.exceptions"] = core_exceptions_module
    sys.modules["azure.cosmos"] = cosmos_module
    sys.modules["azure.identity"] = identity_module
    sys.modules["azure.keyvault"] = keyvault_module
    sys.modules["azure.keyvault.keys"] = keys_module
    sys.modules["azure.keyvault.keys.crypto"] = crypto_module
    sys.modules["azure.storage"] = storage_module
    sys.modules["azure.storage.blob"] = blob_module
    sys.modules["azure.monitor"] = monitor_module
    sys.modules["azure.monitor.opentelemetry"] = opentelemetry_module
    sys.modules["cryptography"] = cryptography_module
    sys.modules["cryptography.hazmat"] = hazmat_module
    sys.modules["cryptography.hazmat.primitives"] = primitives_module
    sys.modules["cryptography.hazmat.primitives.hashes"] = hashes_module
    sys.modules["cryptography.hazmat.primitives.serialization"] = serialization_module
    sys.modules["cryptography.hazmat.primitives.asymmetric"] = asymmetric_module
    sys.modules["cryptography.hazmat.primitives.asymmetric.padding"] = padding_module
    sys.modules["cryptography.hazmat.primitives.ciphers"] = ciphers_module
    sys.modules["cryptography.hazmat.primitives.ciphers.aead"] = aead_module
    sys.modules["reportlab"] = reportlab_module
    sys.modules["reportlab.lib"] = lib_module
    sys.modules["reportlab.lib.colors"] = colors_module
    sys.modules["reportlab.lib.pagesizes"] = pagesizes_module
    sys.modules["reportlab.lib.styles"] = styles_module
    sys.modules["reportlab.platypus"] = platypus_module
    sys.modules["worker_container.email_service"] = email_service_module
    sys.modules["email_service"] = email_service_module


def _load_worker_module():
    _install_worker_dependency_stubs()
    module_name = "test_worker_container_module"
    module_path = REPO_ROOT / "worker_container" / "app.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class WorkerHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_worker_module()

    def test_safe_file_name_strips_directories_and_generates_fallback(self) -> None:
        self.assertEqual(self.module._safe_file_name("../secret.txt"), "secret.txt")
        with patch.object(self.module, "uuid4", return_value=types.SimpleNamespace(hex="abc123")):
            self.assertEqual(self.module._safe_file_name("   "), "file-abc123.bin")

    def test_normalized_recipients_deduplicates_and_normalizes_emails(self) -> None:
        vault_document = {
            "recipients": [
                {"email": " Alice@example.com ", "can_activate": True},
                {"email": "alice@example.com", "can_activate": False},
                "Bob@Example.com",
                {"email": ""},
            ]
        }

        result = self.module._normalized_recipients(vault_document)

        self.assertEqual(
            result,
            [
                {"email": "alice@example.com", "can_activate": True},
                {"email": "bob@example.com", "can_activate": True},
            ],
        )

    def test_build_delivery_zip_name_ascii_normalizes_and_trims(self) -> None:
        vault_document = {
            "name": "Última Escrita: Família / Teste",
            "short_id": "AbC12345",
        }

        result = self.module._build_delivery_zip_name(vault_document)

        self.assertEqual(result, "Ultima Escrita- Familia - Teste-abc12345.zip")

    def test_resolve_unique_path_avoids_name_collisions(self) -> None:
        base_dir = Path("C:/virtual")

        def _fake_exists(path_self: Path) -> bool:
            return path_self.name in {"report.txt", "report-1.txt"}

        with patch.object(Path, "exists", _fake_exists):
            result = self.module._resolve_unique_path(base_dir, "report.txt")

        self.assertEqual(result.name, "report-2.txt")

    def test_container_name_for_vault_sanitizes_and_bounds_result(self) -> None:
        result = self.module._container_name_for_vault("VAULT__ID!!with..weird***chars")
        short_result = self.module._container_name_for_vault("x")

        self.assertTrue(result.startswith("vault-"))
        self.assertLessEqual(len(result), 63)
        self.assertEqual(short_result, "vault-x")

    def test_build_delivery_document_filters_invalid_packages_and_assigns_types(self) -> None:
        vault_document = {
            "id": "vault-123",
            "user_id": "user-123",
            "short_id": "abc12345",
            "name": "My Vault",
            "status": "DELIVERED_ARCHIVED",
            "delivery_packages": [{"recipient_email": "alice@example.com"}, "bad"],
        }

        result = self.module._build_delivery_document(vault_document)

        self.assertEqual(result["id"], "delivery:vault-123")
        self.assertEqual(result["doc_type"], "delivery")
        self.assertEqual(result["type"], "delivery")
        self.assertEqual(result["status"], "delivered_archived")
        self.assertEqual(result["delivery_packages"], [{"recipient_email": "alice@example.com"}])

    def test_load_vault_file_payload_uses_ciphertext_for_zero_knowledge_files(self) -> None:
        file_metadata = {
            "zero_knowledge": True,
            "container_name": "vault-files",
            "blob_name": "cipher.bin",
        }

        with patch.object(self.module, "_download_blob_bytes", return_value=b"ciphertext") as download_mock:
            payload = self.module._load_vault_file_payload(file_metadata)

        self.assertEqual(payload["ciphertext"], b"ciphertext")
        self.assertEqual(payload["metadata"], file_metadata)
        download_mock.assert_called_once_with(
            container_name="vault-files",
            blob_name="cipher.bin",
        )

    def test_load_vault_file_payload_uses_plaintext_for_legacy_files(self) -> None:
        file_metadata = {"zero_knowledge": False, "file_name": "report.txt"}

        with patch.object(self.module, "_decrypt_vault_file", return_value=b"plaintext") as decrypt_mock:
            payload = self.module._load_vault_file_payload(file_metadata)

        self.assertEqual(payload["plaintext"], b"plaintext")
        self.assertEqual(payload["metadata"], file_metadata)
        decrypt_mock.assert_called_once_with(file_metadata)

    def test_upsert_local_delivery_replaces_existing_delivery_document(self) -> None:
        vault_document = {
            "id": "vault-123",
            "user_id": "user-123",
            "name": "Updated Vault",
            "status": "delivered_archived",
            "delivery_packages": [{"recipient_email": "alice@example.com"}],
        }
        existing_items = [
            {
                "id": "delivery:vault-123",
                "doc_type": "delivery",
                "type": "delivery",
                "vault_id": "vault-123",
                "vault_name": "Old Vault",
                "status": "delivery_initiated",
            }
        ]
        saved_items: list[dict] = []

        def _capture_save(items: list[dict]) -> None:
            saved_items[:] = items

        with patch.object(self.module, "_load_local_delivery_items", return_value=existing_items):
            with patch.object(self.module, "_save_local_delivery_items", side_effect=_capture_save):
                result = self.module._upsert_local_delivery(vault_document)

        self.assertEqual(result["vault_name"], "Updated Vault")
        self.assertEqual(saved_items[0]["status"], "delivered_archived")
        self.assertEqual(saved_items[0]["delivery_packages"], [{"recipient_email": "alice@example.com"}])


if __name__ == "__main__":
    unittest.main()
