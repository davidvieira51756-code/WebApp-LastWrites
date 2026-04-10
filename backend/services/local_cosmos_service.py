from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4


class LocalCosmosService:
    def __init__(self, data_file: Optional[str] = None) -> None:
        default_path = Path(__file__).resolve().parents[1] / ".local_data" / "vaults.json"
        self._data_file = Path(data_file) if data_file else default_path

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

    def create_vault(self, vault_data: Dict[str, Any]) -> Dict[str, Any]:
        if not vault_data.get("user_id"):
            raise ValueError("vault_data must include user_id.")

        items = self._read_items()
        payload = dict(vault_data)
        payload["id"] = str(payload.get("id") or uuid4())
        payload.setdefault("recipients", [])
        payload.setdefault("files", [])
        items.append(payload)
        self._write_items(items)
        return payload

    def get_vault_by_id(self, vault_id: str) -> Optional[Dict[str, Any]]:
        items = self._read_items()
        return next((item for item in items if str(item.get("id")) == vault_id), None)

    def list_vaults(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        items = self._read_items()
        if not user_id:
            return items
        return [item for item in items if item.get("user_id") == user_id]

    def add_recipient_to_vault(self, vault_id: str, email: str) -> Optional[Dict[str, Any]]:
        items = self._read_items()
        normalized_email = email.strip().lower()

        for item in items:
            if str(item.get("id")) != vault_id:
                continue

            recipients = item.get("recipients", [])
            if not isinstance(recipients, list):
                recipients = []

            if not any(
                isinstance(recipient, str)
                and recipient.strip().lower() == normalized_email
                for recipient in recipients
            ):
                recipients.append(normalized_email)

            item["recipients"] = recipients
            self._write_items(items)
            return item

        return None

    def remove_recipient_from_vault(self, vault_id: str, email: str) -> Optional[Dict[str, Any]]:
        items = self._read_items()
        normalized_email = email.strip().lower()

        for item in items:
            if str(item.get("id")) != vault_id:
                continue

            recipients = item.get("recipients", [])
            if not isinstance(recipients, list):
                recipients = []

            item["recipients"] = [
                recipient
                for recipient in recipients
                if not (
                    isinstance(recipient, str)
                    and recipient.strip().lower() == normalized_email
                )
            ]
            self._write_items(items)
            return item

        return None

    def get_vault_files(self, vault_id: str) -> Optional[List[Dict[str, Any]]]:
        vault = self.get_vault_by_id(vault_id)
        if vault is None:
            return None

        files = vault.get("files", [])
        if not isinstance(files, list):
            return []
        return [file_item for file_item in files if isinstance(file_item, dict)]

    def remove_file_from_vault(self, vault_id: str, file_id: str) -> Optional[Dict[str, Any]]:
        items = self._read_items()

        for item in items:
            if str(item.get("id")) != vault_id:
                continue

            files = item.get("files", [])
            if not isinstance(files, list):
                files = []

            item["files"] = [
                file_item
                for file_item in files
                if not (
                    isinstance(file_item, dict)
                    and str(file_item.get("id")) == file_id
                )
            ]
            self._write_items(items)
            return item

        return None

    def update_vault(self, vault_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        items = self._read_items()

        for index, item in enumerate(items):
            if str(item.get("id")) != vault_id:
                continue

            updated_item = dict(item)
            updated_item.update(update_data)
            updated_item["id"] = item["id"]
            updated_item["user_id"] = item["user_id"]
            items[index] = updated_item
            self._write_items(items)
            return updated_item

        return None

    def delete_vault(self, vault_id: str) -> bool:
        items = self._read_items()
        remaining = [item for item in items if str(item.get("id")) != vault_id]
        if len(remaining) == len(items):
            return False

        self._write_items(remaining)
        return True
