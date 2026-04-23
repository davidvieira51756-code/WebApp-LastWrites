from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import PurePath
from typing import Any, BinaryIO, Dict, Optional
from uuid import uuid4

from azure.core.exceptions import AzureError, ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import (
    BlobSasPermissions,
    BlobServiceClient,
    ContentSettings,
    generate_blob_sas,
)

logger = logging.getLogger(__name__)


class BlobService:
    def __init__(self, connection_string: Optional[str] = None) -> None:
        self._connection_string = connection_string or os.getenv("BLOB_CONNECTION_STRING")
        if not self._connection_string:
            raise ValueError("Environment variable BLOB_CONNECTION_STRING is required.")

        self._blob_service_client = BlobServiceClient.from_connection_string(
            self._connection_string
        )
        connection_parts = self._parse_connection_string(self._connection_string)
        self._account_name = (
            self._blob_service_client.account_name or connection_parts.get("AccountName")
        )
        self._account_key = os.getenv("BLOB_ACCOUNT_KEY") or connection_parts.get(
            "AccountKey"
        )

        if not self._account_name:
            raise ValueError(
                "Could not determine storage account name from BLOB_CONNECTION_STRING."
            )

    @staticmethod
    def _parse_connection_string(connection_string: str) -> Dict[str, str]:
        parsed: Dict[str, str] = {}
        for segment in connection_string.split(";"):
            if not segment or "=" not in segment:
                continue
            key, value = segment.split("=", 1)
            parsed[key] = value
        return parsed

    @staticmethod
    def _container_name_for_vault(vault_id: str) -> str:
        cleaned = re.sub(r"[^a-z0-9-]", "-", vault_id.lower())
        cleaned = re.sub(r"-+", "-", cleaned).strip("-")

        container_name = f"vault-{cleaned}" if cleaned else "vault-default"
        container_name = container_name[:63].rstrip("-")

        if not container_name:
            container_name = "vault-default"
        if len(container_name) < 3:
            container_name = (container_name + "000")[:3]
        if not container_name[-1].isalnum():
            container_name = f"{container_name[:-1]}0"

        return container_name

    def upload_file(
        self,
        vault_id: str,
        file_stream: BinaryIO,
        file_name: str,
        content_type: Optional[str] = None,
        file_size: Optional[int] = None,
        blob_content_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        container_name = self._container_name_for_vault(vault_id)
        safe_file_name = PurePath(file_name).name if file_name else "upload.bin"
        blob_name = f"{uuid4().hex}.blob"

        container_client = self._blob_service_client.get_container_client(container_name)
        try:
            container_client.create_container()
            logger.info("Created blob container %s", container_name)
        except ResourceExistsError:
            logger.debug("Blob container already exists: %s", container_name)
        except AzureError:
            logger.exception("Failed to create blob container %s", container_name)
            raise

        try:
            file_stream.seek(0)
        except Exception:
            logger.debug("File stream does not support seek; proceeding with current cursor.")

        upload_kwargs: Dict[str, Any] = {}
        effective_blob_content_type = blob_content_type or content_type
        if effective_blob_content_type:
            upload_kwargs["content_settings"] = ContentSettings(
                content_type=effective_blob_content_type
            )
        if file_size is not None and file_size >= 0:
            upload_kwargs["length"] = file_size

        blob_client = container_client.get_blob_client(blob=blob_name)
        try:
            blob_client.upload_blob(file_stream, overwrite=False, **upload_kwargs)
        except AzureError:
            logger.exception(
                "Failed uploading blob for vault_id=%s file_name=%s",
                vault_id,
                safe_file_name,
            )
            raise

        metadata = {
            "id": str(uuid4()),
            "file_name": safe_file_name,
            "blob_name": blob_name,
            "container_name": container_name,
            "blob_url": blob_client.url,
            "content_type": content_type,
            "blob_content_type": effective_blob_content_type,
            "size_bytes": file_size,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(
            "Uploaded file for vault_id=%s blob=%s container=%s",
            vault_id,
            blob_name,
            container_name,
        )
        return metadata

    def upload_bytes(
        self,
        vault_id: str,
        payload: bytes,
        *,
        file_name: str,
        content_type: Optional[str] = None,
        blob_content_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.upload_file(
            vault_id=vault_id,
            file_stream=BytesIO(payload),
            file_name=file_name,
            content_type=content_type,
            file_size=len(payload),
            blob_content_type=blob_content_type,
        )

    def delete_blob(self, container_name: str, blob_name: str) -> None:
        blob_client = self._blob_service_client.get_blob_client(
            container=container_name,
            blob=blob_name,
        )
        try:
            blob_client.delete_blob(delete_snapshots="include")
            logger.info(
                "Deleted blob during rollback. container=%s blob=%s",
                container_name,
                blob_name,
            )
        except ResourceNotFoundError:
            logger.warning(
                "Rollback blob not found. container=%s blob=%s",
                container_name,
                blob_name,
            )
        except AzureError:
            logger.exception(
                "Failed to delete blob during rollback. container=%s blob=%s",
                container_name,
                blob_name,
            )
            raise

    def generate_read_sas_url(
        self,
        container_name: str,
        blob_name: str,
        expires_in_minutes: int = 15,
    ) -> Dict[str, str]:
        if expires_in_minutes < 1:
            raise ValueError("expires_in_minutes must be greater than zero.")

        if not self._account_key:
            raise RuntimeError(
                "Unable to generate SAS URL because storage account key is unavailable. "
                "Configure BLOB_ACCOUNT_KEY or include AccountKey in BLOB_CONNECTION_STRING."
            )

        blob_client = self._blob_service_client.get_blob_client(
            container=container_name,
            blob=blob_name,
        )

        try:
            blob_client.get_blob_properties()
        except ResourceNotFoundError as exc:
            raise FileNotFoundError(
                f"Blob not found. container={container_name} blob={blob_name}"
            ) from exc
        except AzureError:
            logger.exception(
                "Failed to verify blob existence for SAS generation. container=%s blob=%s",
                container_name,
                blob_name,
            )
            raise

        expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes)
        try:
            sas_token = generate_blob_sas(
                account_name=self._account_name,
                container_name=container_name,
                blob_name=blob_name,
                account_key=self._account_key,
                permission=BlobSasPermissions(read=True),
                expiry=expires_at,
                protocol="https",
            )
        except Exception:
            logger.exception(
                "Failed to generate SAS token. container=%s blob=%s",
                container_name,
                blob_name,
            )
            raise

        return {
            "url": f"{blob_client.url}?{sas_token}",
            "expires_at": expires_at.isoformat(),
        }

    def download_blob_bytes(self, container_name: str, blob_name: str) -> bytes:
        blob_client = self._blob_service_client.get_blob_client(
            container=container_name,
            blob=blob_name,
        )
        try:
            return blob_client.download_blob().readall()
        except ResourceNotFoundError as exc:
            raise FileNotFoundError(
                f"Blob not found. container={container_name} blob={blob_name}"
            ) from exc
        except AzureError:
            logger.exception(
                "Failed to download blob bytes. container=%s blob=%s",
                container_name,
                blob_name,
            )
            raise
