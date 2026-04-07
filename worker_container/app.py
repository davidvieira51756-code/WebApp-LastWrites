from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from azure.core.exceptions import AzureError, ResourceExistsError
from azure.storage.blob import BlobServiceClient, ContentSettings

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("last_writes_worker")


def _get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is required.")
    return value


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


def _safe_local_path(base_dir: Path, blob_name: str) -> Path:
    sanitized_parts = [part for part in Path(blob_name).parts if part not in {"", ".", ".."}]
    if not sanitized_parts:
        sanitized_parts = [f"blob-{uuid4().hex}"]

    target_path = (base_dir / Path(*sanitized_parts)).resolve()
    base_dir_resolved = base_dir.resolve()
    if not str(target_path).startswith(str(base_dir_resolved)):
        raise ValueError(f"Unsafe blob path detected: {blob_name}")

    return target_path


def _download_vault_files(
    blob_service_client: BlobServiceClient,
    source_container_name: str,
    download_dir: Path,
) -> List[Path]:
    container_client = blob_service_client.get_container_client(source_container_name)
    logger.info("Listing blobs from source container=%s", source_container_name)

    try:
        blob_items = list(container_client.list_blobs())
    except AzureError:
        logger.exception(
            "Failed to list blobs from source container=%s",
            source_container_name,
        )
        raise

    if not blob_items:
        raise RuntimeError(f"No files found in source container {source_container_name}.")

    downloaded_files: List[Path] = []
    for blob_item in blob_items:
        blob_name = blob_item.name
        target_path = _safe_local_path(download_dir, blob_name)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Downloading blob name=%s size_bytes=%s",
            blob_name,
            getattr(blob_item, "size", "unknown"),
        )
        try:
            downloader = container_client.download_blob(blob_name)
            with target_path.open("wb") as output_file:
                downloader.readinto(output_file)
        except AzureError:
            logger.exception("Failed to download blob name=%s", blob_name)
            raise

        downloaded_files.append(target_path)

    logger.info("Downloaded %s blobs from container=%s", len(downloaded_files), source_container_name)
    return downloaded_files


def _build_zip_archive(downloaded_files: List[Path], base_dir: Path, output_zip: Path) -> None:
    logger.info("Creating ZIP archive at %s", output_zip)
    try:
        with ZipFile(output_zip, mode="w", compression=ZIP_DEFLATED, compresslevel=6) as zip_file:
            for file_path in downloaded_files:
                archive_name = file_path.relative_to(base_dir).as_posix()
                logger.info("Adding file to ZIP archive path=%s archive_name=%s", file_path, archive_name)
                zip_file.write(file_path, arcname=archive_name)
    except Exception:
        logger.exception("Failed to create ZIP archive at %s", output_zip)
        raise

    logger.info("ZIP archive created successfully size_bytes=%s", output_zip.stat().st_size)


def _upload_delivery_zip(
    blob_service_client: BlobServiceClient,
    deliveries_container_name: str,
    vault_id: str,
    zip_path: Path,
) -> Dict[str, str]:
    container_client = blob_service_client.get_container_client(deliveries_container_name)
    try:
        container_client.create_container()
        logger.info("Created deliveries container=%s", deliveries_container_name)
    except ResourceExistsError:
        logger.info("Deliveries container already exists=%s", deliveries_container_name)
    except AzureError:
        logger.exception("Failed to create deliveries container=%s", deliveries_container_name)
        raise

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    blob_name = f"{vault_id}/{timestamp}-delivery.zip"
    blob_client = container_client.get_blob_client(blob=blob_name)

    logger.info(
        "Uploading delivery ZIP container=%s blob=%s size_bytes=%s",
        deliveries_container_name,
        blob_name,
        zip_path.stat().st_size,
    )
    try:
        with zip_path.open("rb") as zip_stream:
            blob_client.upload_blob(
                zip_stream,
                overwrite=True,
                content_settings=ContentSettings(content_type="application/zip"),
                metadata={"vault_id": vault_id, "generated_by": "worker_container"},
            )
    except AzureError:
        logger.exception("Failed to upload delivery ZIP blob=%s", blob_name)
        raise

    logger.info("Delivery ZIP uploaded successfully blob_url=%s", blob_client.url)
    return {
        "container": deliveries_container_name,
        "blob_name": blob_name,
        "blob_url": blob_client.url,
    }


def run() -> int:
    logger.info("Worker startup initiated.")

    try:
        vault_id = _get_required_env("VAULT_ID").strip()
        connection_string = _get_required_env("BLOB_CONNECTION_STRING")
        source_container_name = os.getenv("VAULT_CONTAINER_NAME") or _container_name_for_vault(vault_id)
        deliveries_container_name = os.getenv("DELIVERIES_CONTAINER", "deliveries")
    except Exception:
        logger.exception("Invalid worker configuration.")
        return 1

    logger.info(
        "Worker configuration loaded vault_id=%s source_container=%s deliveries_container=%s",
        vault_id,
        source_container_name,
        deliveries_container_name,
    )

    try:
        blob_service_client = BlobServiceClient.from_connection_string(connection_string)
    except Exception:
        logger.exception("Failed to initialize BlobServiceClient.")
        return 1

    try:
        with tempfile.TemporaryDirectory(prefix=f"last-writes-{vault_id[:12]}-") as temp_dir:
            temp_root = Path(temp_dir)
            download_dir = temp_root / "vault_files"
            download_dir.mkdir(parents=True, exist_ok=True)
            output_zip = temp_root / f"{vault_id}-delivery.zip"

            downloaded_files = _download_vault_files(
                blob_service_client=blob_service_client,
                source_container_name=source_container_name,
                download_dir=download_dir,
            )
            _build_zip_archive(
                downloaded_files=downloaded_files,
                base_dir=download_dir,
                output_zip=output_zip,
            )
            uploaded_zip = _upload_delivery_zip(
                blob_service_client=blob_service_client,
                deliveries_container_name=deliveries_container_name,
                vault_id=vault_id,
                zip_path=output_zip,
            )

            logger.info(
                "Worker completed successfully. vault_id=%s uploaded_blob=%s",
                vault_id,
                uploaded_zip["blob_name"],
            )
            return 0
    except Exception:
        logger.exception("Worker execution failed for vault_id=%s", vault_id)
        return 1


if __name__ == "__main__":
    sys.exit(run())
