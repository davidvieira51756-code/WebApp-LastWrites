from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import unicodedata
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

from email_service import EmailService

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("last_writes_worker")

try:
    from azure.monitor.opentelemetry import configure_azure_monitor
except Exception:  # pragma: no cover - optional dependency resolution
    configure_azure_monitor = None

_monitoring_configured = False


def _configure_monitoring() -> None:
    global _monitoring_configured

    if _monitoring_configured:
        return

    connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "").strip()
    if not connection_string or configure_azure_monitor is None:
        return

    try:
        configure_azure_monitor(connection_string=connection_string)
        _monitoring_configured = True
    except Exception:
        logger.exception("Failed to configure Application Insights telemetry in worker.")


def _env_to_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


FINAL_DELIVERY_STATUSES = {"delivered", "delivered_archived"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date_from_iso(raw_value: str) -> str:
    try:
        return datetime.fromisoformat(raw_value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return raw_value.split("T", 1)[0]


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


def _recipient_email(recipient: Any) -> str:
    if isinstance(recipient, dict):
        return str(recipient.get("email", "")).strip().lower()
    if isinstance(recipient, str):
        return recipient.strip().lower()
    return ""


def _normalized_recipients(vault_document: Dict[str, Any]) -> List[Dict[str, Any]]:
    recipients = vault_document.get("recipients", [])
    if not isinstance(recipients, list):
        recipients = []

    normalized_recipients: List[Dict[str, Any]] = []
    seen_recipients = set()
    for recipient in recipients:
        normalized_email = _recipient_email(recipient)
        if not normalized_email or normalized_email in seen_recipients:
            continue
        seen_recipients.add(normalized_email)
        normalized_recipients.append(
            {
                "email": normalized_email,
                "can_activate": bool(recipient.get("can_activate", True)) if isinstance(recipient, dict) else True,
            }
        )
    return normalized_recipients


def _build_delivery_zip_name(vault_document: Dict[str, Any]) -> str:
    vault_name = str(vault_document.get("name", "vault")).strip() or "vault"
    short_id = str(vault_document.get("short_id", "")).strip().lower()
    base_name = f"{short_id}-{vault_name}" if short_id else vault_name
    normalized_base_name = unicodedata.normalize("NFKD", base_name).encode("ascii", errors="ignore").decode("ascii")
    normalized_base_name = re.sub(r"[^A-Za-z0-9._() -]+", "-", normalized_base_name)
    normalized_base_name = re.sub(r"\s+", " ", normalized_base_name).strip(" .-_") or "vault-delivery"
    return f"{normalized_base_name[:140]}.zip"


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


def _get_local_user(user_id: str) -> Optional[Dict[str, Any]]:
    items = _load_local_items()
    return next(
        (
            item
            for item in items
            if str(item.get("doc_type", "")).strip().lower() == "user"
            and str(item.get("id", "")) == user_id
        ),
        None,
    )


_cosmos_client: Optional[CosmosClient] = None
_vaults_container = None
_audit_container = None


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


def _get_azure_audit_container():
    global _cosmos_client
    global _audit_container

    if _audit_container is not None:
        return _audit_container

    connection_string = _get_required_env("COSMOS_CONNECTION_STRING")
    database_name = os.getenv("COSMOS_DATABASE_NAME", "last-writes-db")
    container_name = os.getenv("COSMOS_AUDIT_CONTAINER", "audit_logs")

    if _cosmos_client is None:
        _cosmos_client = CosmosClient.from_connection_string(connection_string)

    database_client = _cosmos_client.get_database_client(database_name)
    _audit_container = database_client.get_container_client(container_name)
    return _audit_container


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


def _get_azure_user(user_id: str) -> Optional[Dict[str, Any]]:
    container = _get_azure_cosmos_container()
    query = "SELECT * FROM c WHERE c.id = @user_id AND c.doc_type = 'user'"
    parameters = [{"name": "@user_id", "value": user_id}]
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


def _load_user(user_id: str) -> Optional[Dict[str, Any]]:
    if not user_id:
        return None
    if _is_local_dev_mode():
        return _get_local_user(user_id)
    return _get_azure_user(user_id)


def _build_owner_full_name(user_item: Optional[Dict[str, Any]]) -> Optional[str]:
    if not user_item:
        return None

    full_name = str(user_item.get("full_name", "")).strip()
    username = str(user_item.get("username", "")).strip()

    if full_name:
        return full_name
    if username:
        return username
    return None


def _audit_partition_key(vault_id: Optional[str], owner_user_id: str) -> str:
    normalized_vault_id = str(vault_id or "").strip()
    if normalized_vault_id:
        return f"vault:{normalized_vault_id}"
    return f"user:{owner_user_id}"


def _record_local_audit_event(audit_item: Dict[str, Any]) -> None:
    items = _load_local_items()
    items.append(audit_item)
    _save_local_items(items)


def _record_azure_audit_event(audit_item: Dict[str, Any]) -> None:
    container = _get_azure_audit_container()
    container.create_item(body=audit_item)


def _record_audit_event(
    *,
    event_type: str,
    owner_user_id: str,
    vault_id: Optional[str],
    actor_email: Optional[str],
    source: str,
    metadata: Optional[Dict[str, Any]] = None,
    event_at: Optional[str] = None,
) -> None:
    audit_item = {
        "id": str(uuid4()),
        "doc_type": "audit_log",
        "partition_key": _audit_partition_key(vault_id, owner_user_id),
        "event_type": str(event_type).strip(),
        "event_at": event_at or _now_iso(),
        "owner_user_id": str(owner_user_id).strip(),
        "vault_id": str(vault_id).strip() if vault_id is not None else None,
        "actor_user_id": None,
        "actor_email": str(actor_email).strip().lower() if actor_email else None,
        "source": str(source).strip() or "worker",
        "metadata": metadata or {},
    }

    try:
        if _is_local_dev_mode():
            _record_local_audit_event(audit_item)
        else:
            _record_azure_audit_event(audit_item)
    except Exception:
        logger.exception(
            "Failed to persist worker audit event. event_type=%s vault_id=%s",
            event_type,
            vault_id,
        )


def _recipient_access_url(public_vault_id: str) -> Optional[str]:
    frontend_base_url = os.getenv("FRONTEND_BASE_URL", "").strip().rstrip("/")
    if not frontend_base_url:
        return None
    return f"{frontend_base_url}/incoming/{public_vault_id}"


def _send_delivery_notification(vault_document: Dict[str, Any]) -> None:
    email_service = EmailService()
    if not email_service.is_configured():
        logger.warning("ACS email configuration is missing; delivery notification skipped.")
        return

    recipients = [
        recipient["email"]
        for recipient in _normalized_recipients(vault_document)
        if recipient["email"]
    ]
    if not recipients:
        logger.warning("Delivery notification skipped because recipients list is empty.")
        return

    vault_id = str(vault_document.get("id", "")).strip()
    public_vault_id = str(vault_document.get("short_id", "")).strip()
    vault_name = str(vault_document.get("name", "Unnamed Vault")).strip() or "Unnamed Vault"
    access_url = _recipient_access_url(public_vault_id) if public_vault_id else None
    delivered_at = str(vault_document.get("delivered_at", "")).strip() or _now_iso()
    plain_text_lines = [
        f"The delivery package for vault '{vault_name}' is now available.",
        f"Delivered at: {delivered_at}",
    ]
    html_lines = [
        f"<p>The delivery package for vault <strong>{vault_name}</strong> is now available.</p>",
        f"<p>Delivered at: {delivered_at}</p>",
    ]
    if access_url:
        plain_text_lines.append(f"Access it here after signing in: {access_url}")
        html_lines.append(
            f"<p>Access it here after signing in: <a href=\"{access_url}\">{access_url}</a></p>"
        )

    subject = f"[Last Writes] Delivery package available for '{vault_name}'"
    plain_text = "\n".join(plain_text_lines)
    html = "".join(html_lines)
    owner_user_id = str(vault_document.get("user_id", "")).strip()

    for recipient in recipients:
        send_result = email_service.send_email(
            recipient=recipient,
            subject=subject,
            plain_text=plain_text,
            html=html,
        )
        if send_result.sent:
            logger.info(
                "Delivery notification sent. vault_id=%s recipient=%s message_id=%s",
                vault_id,
                recipient,
                send_result.message_id,
            )
            _record_audit_event(
                event_type="delivery_email_sent",
                owner_user_id=owner_user_id,
                vault_id=vault_id,
                actor_email=recipient,
                source="worker",
                metadata={
                    "recipient_email": recipient,
                    "message_id": send_result.message_id,
                },
            )
        elif send_result.failed:
            logger.warning(
                "Delivery notification failed. vault_id=%s recipient=%s error=%s",
                vault_id,
                recipient,
                send_result.error,
            )
            _record_audit_event(
                event_type="email_send_failed",
                owner_user_id=owner_user_id,
                vault_id=vault_id,
                actor_email=recipient,
                source="worker",
                metadata={
                    "email_kind": "delivery",
                    "recipient_email": recipient,
                    "error": send_result.error,
                },
            )


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


def _upload_delivery_zip(vault_document: Dict[str, Any], recipient_email: str, zip_path: Path) -> Dict[str, Any]:
    vault_id = str(vault_document.get("id", "")).strip()
    deliveries_container_name = os.getenv("DELIVERIES_CONTAINER", "deliveries")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    normalized_recipient_email = recipient_email.strip().lower()
    recipient_slug = re.sub(r"[^a-z0-9]+", "-", normalized_recipient_email).strip("-") or "recipient"
    blob_name = f"{vault_id}/{recipient_slug}/{timestamp}-delivery.zip"
    delivery_file_name = _build_delivery_zip_name(vault_document)

    if _is_local_dev_mode():
        root_dir = _get_local_blob_root()
        target_dir = root_dir / deliveries_container_name / vault_id / recipient_slug
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{timestamp}-delivery.zip"
        target_path.write_bytes(zip_path.read_bytes())
        return {
            "recipient_email": normalized_recipient_email,
            "container_name": deliveries_container_name,
            "blob_name": f"{vault_id}/{recipient_slug}/{target_path.name}",
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
            metadata={
                "vault_id": vault_id,
                "recipient_email": normalized_recipient_email,
                "generated_by": "worker_container",
            },
        )

    zip_bytes = zip_path.read_bytes()
    return {
        "recipient_email": normalized_recipient_email,
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


def _generate_cover_pdf(
    vault_document: Dict[str, Any],
    file_items: List[Dict[str, Any]],
    output_path: Path,
    delivered_at: str,
    owner_display_name: Optional[str],
) -> None:
    styles = getSampleStyleSheet()
    story: List[Any] = []

    story.append(Paragraph("Last Writes Delivery Package", styles["Title"]))
    story.append(Spacer(1, 12))
    story.append(Paragraph(f"Vault: {vault_document.get('name', 'Unnamed Vault')}", styles["Heading2"]))
    story.append(Paragraph(f"Delivered: {_date_from_iso(delivered_at)}", styles["BodyText"]))
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

    story.append(Paragraph("From", styles["Heading3"]))
    story.append(Paragraph(owner_display_name or "Unknown owner", styles["BodyText"]))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Included Files", styles["Heading3"]))
    table_rows: List[List[str]] = [["File Name"]]
    for file_item in file_items:
        table_rows.append(
            [
                str(file_item.get("file_name", "Unnamed file")),
            ]
        )

    table = Table(table_rows, repeatRows=1, colWidths=[520])
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
        zip_file.write(cover_pdf_path, arcname="Delivery.pdf")
        for file_path in extracted_files:
            zip_file.write(file_path, arcname=file_path.name)


def run() -> int:
    _configure_monitoring()
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

    existing_delivery_packages = vault_document.get("delivery_packages", [])
    if not isinstance(existing_delivery_packages, list):
        existing_delivery_packages = []

    if (
        str(vault_document.get("status", "")).strip().lower() in FINAL_DELIVERY_STATUSES
        and (
            str(vault_document.get("delivery_blob_name", "")).strip()
            or any(
                isinstance(package, dict)
                and str(package.get("container_name", "")).strip()
                and str(package.get("blob_name", "")).strip()
                for package in existing_delivery_packages
            )
        )
    ):
        logger.info("Vault already delivered. Nothing to do. vault_id=%s", vault_id)
        return 0

    files = vault_document.get("files", [])
    if not isinstance(files, list):
        files = []

    try:
        recipients = _normalized_recipients(vault_document)
        if not recipients:
            raise ValueError("Vault has no recipients configured for delivery.")
        owner_profile = _load_user(str(vault_document.get("user_id", "")).strip())
        owner_display_name = _build_owner_full_name(owner_profile)

        with tempfile.TemporaryDirectory(prefix=f"last-writes-{vault_id[:12]}-") as temp_dir:
            temp_root = Path(temp_dir)
            delivered_at = _now_iso()
            decrypted_payloads: List[Dict[str, Any]] = []
            for file_item in files:
                if not isinstance(file_item, dict):
                    continue
                decrypted_payloads.append(
                    {
                        "metadata": file_item,
                        "plaintext": _decrypt_vault_file(file_item),
                    }
                )

            delivery_packages: List[Dict[str, Any]] = []
            for recipient in recipients:
                recipient_email = recipient["email"]
                recipient_dir = temp_root / re.sub(r"[^a-z0-9]+", "-", recipient_email).strip("-")
                extracted_dir = recipient_dir / "decrypted_files"
                extracted_dir.mkdir(parents=True, exist_ok=True)
                cover_pdf_path = recipient_dir / "cover.pdf"
                output_zip = recipient_dir / "delivery.zip"

                recipient_files = [
                    payload
                    for payload in decrypted_payloads
                    if recipient_email in (
                        [
                            str(candidate).strip().lower()
                            for candidate in payload["metadata"].get("recipient_emails", [])
                        ]
                        if isinstance(payload["metadata"].get("recipient_emails"), list)
                        else []
                    )
                ]

                extracted_files: List[Path] = []
                for payload in recipient_files:
                    target_path = _resolve_unique_path(
                        extracted_dir,
                        str(payload["metadata"].get("file_name", "")),
                    )
                    target_path.write_bytes(payload["plaintext"])
                    extracted_files.append(target_path)

                _generate_cover_pdf(
                    vault_document=vault_document,
                    file_items=[payload["metadata"] for payload in recipient_files],
                    output_path=cover_pdf_path,
                    delivered_at=delivered_at,
                    owner_display_name=owner_display_name,
                )
                _build_zip_archive(
                    cover_pdf_path=cover_pdf_path,
                    extracted_files=extracted_files,
                    output_zip=output_zip,
                )

                upload_result = _upload_delivery_zip(
                    vault_document=vault_document,
                    recipient_email=recipient_email,
                    zip_path=output_zip,
                )
                upload_result["delivered_at"] = delivered_at
                delivery_packages.append(upload_result)

            primary_package = delivery_packages[0] if delivery_packages else {}
            updated_vault = _update_vault(
                vault_id,
                {
                    "status": "delivered_archived",
                    "delivery_container_name": primary_package.get("container_name"),
                    "delivery_blob_name": primary_package.get("blob_name"),
                    "delivery_file_name": primary_package.get("file_name"),
                    "delivery_size_bytes": primary_package.get("size_bytes"),
                    "delivery_checksum_sha256": primary_package.get("checksum_sha256"),
                    "delivery_packages": delivery_packages,
                    "delivery_error": None,
                    "delivered_at": delivered_at,
                    "delivery_job_execution_name": execution_name,
                },
            )
            if updated_vault is None:
                raise RuntimeError("Vault metadata could not be updated after delivery ZIP upload.")

            _record_audit_event(
                event_type="delivery_completed",
                owner_user_id=str(updated_vault.get("user_id", "")).strip(),
                vault_id=vault_id,
                actor_email=None,
                source="worker",
                metadata={
                    "delivery_package_count": len(delivery_packages),
                    "delivery_blob_name": primary_package.get("blob_name"),
                    "delivery_container_name": primary_package.get("container_name"),
                    "delivery_size_bytes": primary_package.get("size_bytes"),
                },
                event_at=delivered_at,
            )
            try:
                _send_delivery_notification(updated_vault)
            except Exception:
                logger.exception(
                    "Delivery notification failed after ZIP creation. vault_id=%s",
                    vault_id,
                )

            logger.info(
                "Worker completed successfully. vault_id=%s delivery_packages=%s primary_delivery_blob=%s",
                vault_id,
                len(delivery_packages),
                primary_package.get("blob_name"),
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
