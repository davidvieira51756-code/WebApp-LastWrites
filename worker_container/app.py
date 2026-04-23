from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from azure.core.exceptions import AzureError, ResourceExistsError
from azure.cosmos import CosmosClient, exceptions
from azure.identity import DefaultAzureCredential
from azure.keyvault.keys.crypto import CryptographyClient, EncryptionAlgorithm
from azure.storage.blob import BlobServiceClient, ContentSettings
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("last_writes_worker")


def _env_to_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is required.")
    return value


def _is_local_dev_mode() -> bool:
    return _env_to_bool(os.getenv("LOCAL_DEV_MODE", "false"))


def _b64url_decode(raw: str) -> bytes:
    padding_length = (-len(raw)) % 4
    return base64.urlsafe_b64decode(f"{raw}{'=' * padding_length}".encode("ascii"))


def _sha256_hexdigest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _default_local_data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "backend" / ".local_data"


def _get_local_blob_root() -> Path:
    return Path(os.getenv("LOCAL_BLOB_ROOT_DIR", str(_default_local_data_dir() / "blobs")))


def _get_local_cosmos_file() -> Path:
    return Path(os.getenv("LOCAL_COSMOS_DATA_FILE", str(_default_local_data_dir() / "vaults.json")))


def _get_local_keys_dir() -> Path:
    return Path(os.getenv("LOCAL_VAULT_KEYS_DIR", str(_default_local_data_dir() / "vault_keys")))


def _safe_file_name(file_name: str) -> str:
    safe_name = Path(file_name).name.strip()
    return safe_name or f"file-{uuid4().hex}.bin"


def _resolve_unique_path(base_dir: Path, file_name: str) -> Path:
    safe_name = _safe_file_name(file_name)
    stem = Path(safe_name).stem
    suffix = Path(safe_name).suffix
    candidate = base_dir / safe_name
    counter = 1

    while candidate.exists():
        candidate = base_dir / f"{stem}-{counter}{suffix}"
        counter += 1

    return candidate


def _container_name_for_vault(vault_id: str) -> str:
    cleaned = re.sub(r"[^a-z0-9-]", "-", vault_id.lower())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")

    base_name = f"vault-{cleaned}" if cleaned else "vault-default"
    base_name = base_name[:63].rstrip("-")

    if not base_name:
        base_name = "vault-default"
    if len(base_name) < 3:
        base_name = (base_name + "000")[:3]
    if not base_name[-1].isalnum():
        base_name = f"{base_name[:-1]}0"

    return base_name


def _load_local_items() -> List[Dict[str, Any]]:
    data_file = _get_local_cosmos_file()
    if not data_file.exists():
        return []
    raw = data_file.read_text(encoding="utf-8").strip() or "[]"
    items = json.loads(raw)
    return [item for item in items if isinstance(item, dict)]


def _save_local_items(items: List[Dict[str, Any]]) -> None:
    data_file = _get_local_cosmos_file()
    data_file.parent.mkdir(parents=True, exist_ok=True)
    data_file.write_text(json.dumps(items, indent=2), encoding="utf-8")


def _get_local_vault(vault_id: str) -> Optional[Dict[str, Any]]:
    items = _load_local_items()
    return next(
        (
            item
            for item in items
            if str(item.get("doc_type", "")).strip().lower() == "vault"
            and str(item.get("id", "")) == vault_id
        ),
        None,
    )


def _update_local_vault(vault_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    items = _load_local_items()
    for index, item in enumerate(items):
        if str(item.get("doc_type", "")).strip().lower() != "vault":
            continue
        if str(item.get("id", "")) != vault_id:
            continue

        updated_item = dict(item)
        updated_item.update(update_data)
        items[index] = updated_item
        _save_local_items(items)
        return updated_item
    return None


_cosmos_client: Optional[CosmosClient] = None
_vaults_container = None


def _get_azure_cosmos_container():
    global _cosmos_client
    global _vaults_container

    if _vaults_container is not None:
        return _vaults_container

    connection_string = _get_required_env("COSMOS_CONNECTION_STRING")
    database_name = os.getenv("COSMOS_DATABASE_NAME", "last-writes-db")
    container_name = os.getenv("COSMOS_VAULTS_CONTAINER", "vaults")

    _cosmos_client = CosmosClient.from_connection_string(connection_string)
    database_client = _cosmos_client.get_database_client(database_name)
    _vaults_container = database_client.get_container_client(container_name)
    return _vaults_container


def _get_azure_vault(vault_id: str) -> Optional[Dict[str, Any]]:
    container = _get_azure_cosmos_container()
    query = "SELECT * FROM c WHERE c.id = @vault_id AND c.doc_type = 'vault'"
    parameters = [{"name": "@vault_id", "value": vault_id}]
    items = list(
        container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True,
        )
    )
    return items[0] if items else None


def _update_azure_vault(vault_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    container = _get_azure_cosmos_container()
    existing_item = _get_azure_vault(vault_id)
    if existing_item is None:
        return None

    merged_item = dict(existing_item)
    merged_item.update(update_data)
    return container.replace_item(item=existing_item, body=merged_item)


def _load_vault(vault_id: str) -> Optional[Dict[str, Any]]:
    if _is_local_dev_mode():
        return _get_local_vault(vault_id)
    return _get_azure_vault(vault_id)


def _update_vault(vault_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if _is_local_dev_mode():
        return _update_local_vault(vault_id, update_data)
    return _update_azure_vault(vault_id, update_data)


def _download_blob_bytes(container_name: str, blob_name: str) -> bytes:
    if _is_local_dev_mode():
        root_dir = _get_local_blob_root()
        target_path = (root_dir / container_name / Path(blob_name).name).resolve()
        if not target_path.exists():
            raise FileNotFoundError(
                f"Blob not found. container={container_name} blob={blob_name}"
            )
        return target_path.read_bytes()

    connection_string = _get_required_env("BLOB_CONNECTION_STRING")
    blob_service_client = BlobServiceClient.from_connection_string(connection_string)
    blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
    return blob_client.download_blob().readall()


def _upload_delivery_zip(vault_id: str, zip_path: Path) -> Dict[str, Any]:
    deliveries_container_name = os.getenv("DELIVERIES_CONTAINER", "deliveries")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    blob_name = f"{vault_id}/{timestamp}-delivery.zip"
    delivery_file_name = f"{vault_id}-delivery.zip"

    if _is_local_dev_mode():
        root_dir = _get_local_blob_root()
        target_dir = root_dir / deliveries_container_name / vault_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{timestamp}-delivery.zip"
        target_path.write_bytes(zip_path.read_bytes())
        return {
            "container_name": deliveries_container_name,
            "blob_name": f"{vault_id}/{target_path.name}",
            "file_name": delivery_file_name,
            "size_bytes": target_path.stat().st_size,
            "checksum_sha256": _sha256_hexdigest(target_path.read_bytes()),
            "blob_url": str(target_path),
        }

    connection_string = _get_required_env("BLOB_CONNECTION_STRING")
    blob_service_client = BlobServiceClient.from_connection_string(connection_string)
    container_client = blob_service_client.get_container_client(deliveries_container_name)
    try:
        container_client.create_container()
    except ResourceExistsError:
        pass
    except AzureError:
        logger.exception("Failed to create deliveries container=%s", deliveries_container_name)
        raise

    blob_client = container_client.get_blob_client(blob=blob_name)
    with zip_path.open("rb") as zip_stream:
        blob_client.upload_blob(
            zip_stream,
            overwrite=True,
            content_settings=ContentSettings(content_type="application/zip"),
            metadata={"vault_id": vault_id, "generated_by": "worker_container"},
        )

    zip_bytes = zip_path.read_bytes()
    return {
        "container_name": deliveries_container_name,
        "blob_name": blob_name,
        "file_name": delivery_file_name,
        "size_bytes": zip_path.stat().st_size,
        "checksum_sha256": _sha256_hexdigest(zip_bytes),
        "blob_url": blob_client.url,
    }


def _unwrap_file_key(*, key_kid: str, wrapped_key: str) -> bytes:
    if _is_local_dev_mode():
        key_name = key_kid.split("/")[3] if key_kid.startswith("local://") else key_kid.rsplit("/", 2)[-2]
        private_key_path = _get_local_keys_dir() / f"{key_name}.pem"
        if not private_key_path.exists():
            raise FileNotFoundError(f"Local private key file not found for key_name={key_name}.")

        private_key = serialization.load_pem_private_key(
            private_key_path.read_bytes(),
            password=None,
        )
        return private_key.decrypt(
            _b64url_decode(wrapped_key),
            asym_padding.OAEP(
                mgf=asym_padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )

    key_vault_url = _get_required_env("KEY_VAULT_URL")
    credential = DefaultAzureCredential()
    crypto_client = CryptographyClient(key_kid, credential=credential)
    decrypted = crypto_client.decrypt(
        EncryptionAlgorithm.rsa_oaep_256,
        _b64url_decode(wrapped_key),
    )
    return bytes(decrypted.plaintext)


def _decrypt_vault_file(file_metadata: Dict[str, Any]) -> bytes:
    container_name = str(file_metadata.get("container_name", "")).strip()
    blob_name = str(file_metadata.get("blob_name", "")).strip()
    if not container_name or not blob_name:
        raise ValueError("File metadata is missing blob location details.")

    blob_bytes = _download_blob_bytes(container_name=container_name, blob_name=blob_name)
    if not bool(file_metadata.get("encrypted", False)):
        return blob_bytes

    wrapped_key = str(file_metadata.get("wrapped_key", "")).strip()
    iv = str(file_metadata.get("iv", "")).strip()
    tag = str(file_metadata.get("tag", "")).strip()
    key_kid = str(file_metadata.get("key_kid", "")).strip()
    if not wrapped_key or not iv or not tag or not key_kid:
        raise ValueError("Encrypted file metadata is incomplete.")

    aes_key = _unwrap_file_key(key_kid=key_kid, wrapped_key=wrapped_key)
    plaintext = AESGCM(aes_key).decrypt(
        _b64url_decode(iv),
        blob_bytes + _b64url_decode(tag),
        None,
    )

    expected_checksum = str(file_metadata.get("plaintext_sha256", "")).strip()
    if expected_checksum and _sha256_hexdigest(plaintext) != expected_checksum:
        raise ValueError("Plaintext checksum verification failed during worker decryption.")

    return plaintext


def _generate_cover_pdf(vault_document: Dict[str, Any], file_items: List[Dict[str, Any]], output_path: Path) -> None:
    styles = getSampleStyleSheet()
    story: List[Any] = []

    story.append(Paragraph("Last Writes Delivery Package", styles["Title"]))
    story.append(Spacer(1, 12))
    story.append(Paragraph(f"Vault: {vault_document.get('name', 'Unnamed Vault')}", styles["Heading2"]))
    story.append(Paragraph(f"Vault ID: {vault_document.get('id', 'unknown')}", styles["BodyText"]))
    story.append(Paragraph(f"Generated at: {_now_iso()}", styles["BodyText"]))
    story.append(Spacer(1, 12))

    owner_message = str(vault_document.get("owner_message", "")).strip()
    story.append(Paragraph("Owner Message", styles["Heading3"]))
    story.append(
        Paragraph(
            owner_message if owner_message else "No personal message was provided by the vault owner.",
            styles["BodyText"],
        )
    )
    story.append(Spacer(1, 12))

    recipients = vault_document.get("recipients", [])
    recipient_text = ", ".join(str(recipient).strip() for recipient in recipients if str(recipient).strip())
    story.append(Paragraph("Recipients", styles["Heading3"]))
    story.append(
        Paragraph(recipient_text if recipient_text else "No recipients were listed.", styles["BodyText"])
    )
    story.append(Spacer(1, 12))

    story.append(Paragraph("Included Files", styles["Heading3"]))
    table_rows: List[List[str]] = [["File Name", "Type", "Original Size"]]
    for file_item in file_items:
        table_rows.append(
            [
                str(file_item.get("file_name", "Unnamed file")),
                str(file_item.get("content_type", "Unknown") or "Unknown"),
                str(file_item.get("size_bytes", "Unknown")),
            ]
        )

    table = Table(table_rows, repeatRows=1, colWidths=[280, 140, 100])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#18181B")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D4D4D8")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F4F5")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(table)

    document = SimpleDocTemplate(str(output_path), pagesize=A4)
    document.build(story)


def _build_zip_archive(cover_pdf_path: Path, extracted_files: List[Path], output_zip: Path) -> None:
    with ZipFile(output_zip, mode="w", compression=ZIP_DEFLATED, compresslevel=6) as zip_file:
        zip_file.write(cover_pdf_path, arcname="00-cover.pdf")
        for file_path in extracted_files:
            zip_file.write(file_path, arcname=file_path.name)


def run() -> int:
    logger.info("Worker startup initiated.")

    try:
        vault_id = _get_required_env("VAULT_ID").strip()
    except Exception:
        logger.exception("Invalid worker configuration.")
        return 1

    execution_name = os.getenv("CONTAINERAPP_JOB_EXECUTION_NAME", "").strip() or None
    vault_document = _load_vault(vault_id)
    if vault_document is None:
        logger.error("Vault was not found. vault_id=%s", vault_id)
        return 1

    if (
        str(vault_document.get("status", "")).strip().lower() == "delivered"
        and str(vault_document.get("delivery_blob_name", "")).strip()
    ):
        logger.info("Vault already delivered. Nothing to do. vault_id=%s", vault_id)
        return 0

    files = vault_document.get("files", [])
    if not isinstance(files, list):
        files = []

    try:
        with tempfile.TemporaryDirectory(prefix=f"last-writes-{vault_id[:12]}-") as temp_dir:
            temp_root = Path(temp_dir)
            extracted_dir = temp_root / "decrypted_files"
            extracted_dir.mkdir(parents=True, exist_ok=True)
            cover_pdf_path = temp_root / "cover.pdf"
            output_zip = temp_root / f"{vault_id}-delivery.zip"

            extracted_files: List[Path] = []
            for file_item in files:
                if not isinstance(file_item, dict):
                    continue

                plaintext = _decrypt_vault_file(file_item)
                target_path = _resolve_unique_path(extracted_dir, str(file_item.get("file_name", "")))
                target_path.write_bytes(plaintext)
                extracted_files.append(target_path)

            _generate_cover_pdf(vault_document=vault_document, file_items=files, output_path=cover_pdf_path)
            _build_zip_archive(
                cover_pdf_path=cover_pdf_path,
                extracted_files=extracted_files,
                output_zip=output_zip,
            )

            upload_result = _upload_delivery_zip(vault_id=vault_id, zip_path=output_zip)
            updated_vault = _update_vault(
                vault_id,
                {
                    "status": "delivered",
                    "delivery_container_name": upload_result["container_name"],
                    "delivery_blob_name": upload_result["blob_name"],
                    "delivery_file_name": upload_result["file_name"],
                    "delivery_size_bytes": upload_result["size_bytes"],
                    "delivery_checksum_sha256": upload_result["checksum_sha256"],
                    "delivery_error": None,
                    "delivered_at": _now_iso(),
                    "delivery_job_execution_name": execution_name,
                },
            )
            if updated_vault is None:
                raise RuntimeError("Vault metadata could not be updated after delivery ZIP upload.")

            logger.info(
                "Worker completed successfully. vault_id=%s delivery_blob=%s",
                vault_id,
                upload_result["blob_name"],
            )
            return 0
    except Exception as exc:
        logger.exception("Worker execution failed for vault_id=%s", vault_id)
        try:
            _update_vault(
                vault_id,
                {
                    "delivery_error": str(exc),
                    "delivery_job_execution_name": execution_name,
                },
            )
        except Exception:
            logger.exception("Failed to persist worker delivery error for vault_id=%s", vault_id)
        return 1


if __name__ == "__main__":
    sys.exit(run())
