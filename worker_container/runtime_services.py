from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePath
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from azure.cosmos import CosmosClient, PartitionKey, exceptions
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.storage.blob import BlobSasPermissions, BlobServiceClient, generate_blob_sas

logger = logging.getLogger(__name__)

_configured_services: set[str] = set()

CONTROL_CHARS_REGEX = re.compile(r"[\x00-\x1f\x7f]+")
SAFE_FILENAME_REGEX = re.compile(r"[^A-Za-z0-9._ -]+")


def _env_to_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _is_local_dev_mode() -> bool:
    return _env_to_bool(os.getenv("LOCAL_DEV_MODE", "false"))


def configure_application_insights(service_name: str) -> bool:
    connection_string = (
        os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "").strip()
        or os.getenv("APPINSIGHTS_CONNECTIONSTRING", "").strip()
    )
    if not connection_string or service_name in _configured_services:
        return False

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
    except Exception:
        logger.warning(
            "Application Insights SDK is unavailable; telemetry was not configured for service=%s",
            service_name,
        )
        return False

    os.environ.setdefault("OTEL_SERVICE_NAME", service_name)
    configure_azure_monitor(connection_string=connection_string)
    _configured_services.add(service_name)
    return True


def build_key_vault_credential():
    if _is_local_dev_mode():
        return DefaultAzureCredential(exclude_interactive_browser_credential=False)

    client_id = (
        os.getenv("MANAGED_IDENTITY_CLIENT_ID", "").strip()
        or os.getenv("AZURE_CLIENT_ID", "").strip()
        or None
    )
    credential = ManagedIdentityCredential(client_id=client_id)
    credential.get_token("https://vault.azure.net/.default")
    return credential


def sanitize_filename(file_name: str) -> str:
    base_name = PurePath(file_name or "").name
    base_name = CONTROL_CHARS_REGEX.sub("", base_name).strip().strip(". ")
    base_name = SAFE_FILENAME_REGEX.sub("_", base_name)
    base_name = re.sub(r"\s+", " ", base_name).strip()

    if not base_name or base_name in {".", ".."}:
        return f"file-{uuid4().hex}.bin"

    if len(base_name) <= 180:
        return base_name

    stem, dot, suffix = base_name.rpartition(".")
    if dot and suffix:
        suffix = f".{suffix[:20]}"
        stem = stem[: max(1, 180 - len(suffix))]
        return f"{stem}{suffix}"
    return base_name[:180]


def _parse_connection_string(connection_string: str) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for segment in connection_string.split(";"):
        if not segment or "=" not in segment:
            continue
        key, value = segment.split("=", 1)
        parsed[key] = value
    return parsed


class AuditService:
    def __init__(self, connection_string: str) -> None:
        self._connection_string = connection_string
        self._database_name = os.getenv("COSMOS_DATABASE_NAME", "last-writes-db")
        self._container_name = os.getenv("COSMOS_AUDIT_CONTAINER", "audit_logs")
        self._container_throughput = int(os.getenv("COSMOS_AUDIT_CONTAINER_THROUGHPUT", "400"))
        self._client: Optional[CosmosClient] = None
        self._database = None
        self._container = None

    def initialize(self) -> None:
        self._client = CosmosClient.from_connection_string(self._connection_string)
        self._database = self._client.create_database_if_not_exists(id=self._database_name)
        self._container = self._database.create_container_if_not_exists(
            id=self._container_name,
            partition_key=PartitionKey(path="/owner_user_id"),
            offer_throughput=self._container_throughput,
        )

    def _get_container(self):
        if self._container is None:
            raise RuntimeError("AuditService is not initialized.")
        return self._container

    def record_event(
        self,
        *,
        owner_user_id: str,
        event_type: str,
        vault_id: Optional[str] = None,
        actor_type: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        occurred_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "id": str(uuid4()),
            "doc_type": "audit_event",
            "owner_user_id": owner_user_id,
            "vault_id": vault_id,
            "event_type": event_type,
            "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat(),
            "actor_type": actor_type,
            "details": details or {},
        }
        return self._get_container().create_item(body=payload)


class LocalAuditService:
    def __init__(self, data_file: Optional[str] = None) -> None:
        default_path = Path(__file__).resolve().parents[1] / "backend" / ".local_data" / "vaults.json"
        configured_path = data_file or os.getenv("LOCAL_COSMOS_DATA_FILE")
        self._data_file = Path(configured_path) if configured_path else default_path

    def initialize(self) -> None:
        self._data_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._data_file.exists():
            self._data_file.write_text("[]", encoding="utf-8")

    def _read_items(self) -> List[Dict[str, Any]]:
        self.initialize()
        raw = self._data_file.read_text(encoding="utf-8").strip() or "[]"
        items = json.loads(raw)
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    def _write_items(self, items: List[Dict[str, Any]]) -> None:
        self._data_file.write_text(json.dumps(items, indent=2), encoding="utf-8")

    def record_event(
        self,
        *,
        owner_user_id: str,
        event_type: str,
        vault_id: Optional[str] = None,
        actor_type: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        occurred_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        items = self._read_items()
        payload = {
            "id": str(uuid4()),
            "doc_type": "audit_event",
            "owner_user_id": owner_user_id,
            "vault_id": vault_id,
            "event_type": event_type,
            "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat(),
            "actor_type": actor_type,
            "details": details or {},
        }
        items.append(payload)
        self._write_items(items)
        return payload


class DeliveryBlobService:
    def __init__(self, connection_string: Optional[str] = None) -> None:
        self._connection_string = connection_string or os.getenv("BLOB_CONNECTION_STRING", "").strip()
        if not self._connection_string:
            raise ValueError("BLOB_CONNECTION_STRING is required.")

        self._blob_service_client = BlobServiceClient.from_connection_string(self._connection_string)
        connection_parts = _parse_connection_string(self._connection_string)
        self._account_name = self._blob_service_client.account_name or connection_parts.get("AccountName")
        self._account_key = os.getenv("BLOB_ACCOUNT_KEY") or connection_parts.get("AccountKey")

    def generate_read_sas_url(
        self,
        *,
        container_name: str,
        blob_name: str,
        expires_in_minutes: int,
    ) -> Dict[str, str]:
        blob_client = self._blob_service_client.get_blob_client(container=container_name, blob=blob_name)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes)
        sas_token = generate_blob_sas(
            account_name=self._account_name,
            container_name=container_name,
            blob_name=blob_name,
            account_key=self._account_key,
            permission=BlobSasPermissions(read=True),
            expiry=expires_at,
            protocol="https",
        )
        return {
            "url": f"{blob_client.url}?{sas_token}",
            "expires_at": expires_at.isoformat(),
        }


class LocalDeliveryBlobService:
    def __init__(self) -> None:
        default_root = Path(__file__).resolve().parents[1] / "backend" / ".local_data" / "blobs"
        configured_root = os.getenv("LOCAL_BLOB_ROOT_DIR")
        self._root_dir = Path(configured_root) if configured_root else default_root

    def generate_read_sas_url(
        self,
        *,
        container_name: str,
        blob_name: str,
        expires_in_minutes: int,
    ) -> Dict[str, str]:
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes)
        return {
            "url": f"/local-downloads/{container_name}/{blob_name}",
            "expires_at": expires_at.isoformat(),
        }


class DeliveryEmailService:
    def send_delivery_notification(
        self,
        *,
        recipients: Iterable[str],
        vault_name: str,
        vault_id: str,
        download_url: str,
        expires_at: str,
        owner_message: Optional[str],
    ) -> None:
        raise NotImplementedError


class LocalDeliveryEmailService(DeliveryEmailService):
    def send_delivery_notification(
        self,
        *,
        recipients: Iterable[str],
        vault_name: str,
        vault_id: str,
        download_url: str,
        expires_at: str,
        owner_message: Optional[str],
    ) -> None:
        logger.info(
            "LOCAL email dispatch. recipients=%s vault_id=%s vault_name=%s download_url=%s expires_at=%s owner_message=%s",
            list(recipients),
            vault_id,
            vault_name,
            download_url,
            expires_at,
            bool(owner_message),
        )


class AzureCommunicationEmailService(DeliveryEmailService):
    def __init__(self, connection_string: str, sender_address: str) -> None:
        from azure.communication.email import EmailClient

        self._client = EmailClient.from_connection_string(connection_string)
        self._sender_address = sender_address

    def send_delivery_notification(
        self,
        *,
        recipients: Iterable[str],
        vault_name: str,
        vault_id: str,
        download_url: str,
        expires_at: str,
        owner_message: Optional[str],
    ) -> None:
        for recipient in recipients:
            message = {
                "senderAddress": self._sender_address,
                "recipients": {"to": [{"address": recipient}]},
                "content": {
                    "subject": f"[Last Writes] Delivery available for '{vault_name}'",
                    "plainText": "\n".join(
                        [
                            f"A delivery package for vault '{vault_name}' is now available.",
                            f"Download URL: {download_url}",
                            f"Link expires at: {expires_at}",
                            "",
                            f"Owner message: {(owner_message or '').strip()}",
                        ]
                    ).strip(),
                    "html": (
                        f"<html><body><p>A delivery package for vault '<strong>{vault_name}</strong>' is now available.</p>"
                        f"<p><a href=\"{download_url}\">Download delivery package</a></p>"
                        f"<p>This link expires at {expires_at}.</p>"
                        f"<p>{(owner_message or '').strip()}</p></body></html>"
                    ),
                },
            }
            poller = self._client.begin_send(message)
            poller.result()


def build_delivery_email_service() -> DeliveryEmailService:
    if _is_local_dev_mode():
        return LocalDeliveryEmailService()

    connection_string = os.getenv("ACS_EMAIL_CONNECTION_STRING", "").strip()
    sender_address = os.getenv("ACS_EMAIL_SENDER", "").strip()
    if not connection_string or not sender_address:
        raise ValueError("ACS_EMAIL_CONNECTION_STRING and ACS_EMAIL_SENDER are required.")

    return AzureCommunicationEmailService(connection_string, sender_address)
