from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePath
from typing import Any, BinaryIO, Dict, Optional
from uuid import uuid4


class LocalBlobService:
    is_local = True

    def __init__(self, root_dir: Optional[str] = None) -> None:
        default_root = Path(__file__).resolve().parents[1] / ".local_data" / "blobs"
        self._root_dir = Path(root_dir) if root_dir else default_root

    def initialize(self) -> None:
        self._root_dir.mkdir(parents=True, exist_ok=True)

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

    def _resolve_blob_path(self, container_name: str, blob_name: str) -> Path:
        safe_blob_name = PurePath(blob_name).name
        return self._root_dir / container_name / safe_blob_name

    def upload_file(
        self,
        vault_id: str,
        file_stream: BinaryIO,
        file_name: str,
        content_type: Optional[str] = None,
        file_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        self.initialize()
        container_name = self._container_name_for_vault(vault_id)
        safe_file_name = PurePath(file_name).name if file_name else "upload.bin"
        blob_name = f"{uuid4().hex}-{safe_file_name}"

        target_dir = self._root_dir / container_name
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / blob_name

        try:
            file_stream.seek(0)
        except Exception:
            pass

        with target_path.open("wb") as output_file:
            while True:
                chunk = file_stream.read(1024 * 1024)
                if not chunk:
                    break
                output_file.write(chunk)

        if file_size is None:
            file_size = target_path.stat().st_size

        return {
            "id": str(uuid4()),
            "file_name": safe_file_name,
            "blob_name": blob_name,
            "container_name": container_name,
            "blob_url": str(target_path),
            "content_type": content_type,
            "size_bytes": file_size,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }

    def delete_blob(self, container_name: str, blob_name: str) -> None:
        target_path = self._resolve_blob_path(container_name, blob_name)
        if target_path.exists():
            target_path.unlink()

    def generate_read_sas_url(
        self,
        container_name: str,
        blob_name: str,
        expires_in_minutes: int = 15,
    ) -> Dict[str, str]:
        if expires_in_minutes < 1:
            raise ValueError("expires_in_minutes must be greater than zero.")

        target_path = self._resolve_blob_path(container_name, blob_name)
        if not target_path.exists():
            raise FileNotFoundError(
                f"Blob not found. container={container_name} blob={blob_name}"
            )

        expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes)
        return {
            "url": f"/local-downloads/{container_name}/{blob_name}",
            "expires_at": expires_at.isoformat(),
        }

    def get_local_file_path(self, container_name: str, blob_name: str) -> Path:
        path = self._resolve_blob_path(container_name, blob_name)
        if not path.exists() or not str(path.resolve()).startswith(str(self._root_dir.resolve())):
            raise FileNotFoundError(
                f"Blob not found. container={container_name} blob={blob_name}"
            )
        return path
