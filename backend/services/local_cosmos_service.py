from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4


VAULT_ACTIVATION_TERMINAL_STATUSES = {"delivery_initiated", "delivered", "delivered_archived", "disabled"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _recompute_activation_state(vault_document: Dict[str, Any]) -> Dict[str, Any]:
    current_status = str(vault_document.get("status", "active")).strip().lower()
    if current_status in VAULT_ACTIVATION_TERMINAL_STATUSES:
        return vault_document

    activation_requests = vault_document.get("activation_requests", [])
    if not isinstance(activation_requests, list):
        activation_requests = []

    requests_count = len(activation_requests)
    try:
        threshold = max(1, int(vault_document.get("activation_threshold", 1)))
    except (TypeError, ValueError):
        threshold = 1

    try:
        grace_period_days = max(0, int(vault_document.get("grace_period_days", 0)))
    except (TypeError, ValueError):
        grace_period_days = 0

    if requests_count == 0:
        vault_document["status"] = "active"
        vault_document["grace_period_started_at"] = None
        vault_document["grace_period_expires_at"] = None
        vault_document["grace_period_event_published_at"] = None
        return vault_document

    if requests_count < threshold:
        vault_document["status"] = "pending_activation"
        vault_document["grace_period_started_at"] = None
        vault_document["grace_period_expires_at"] = None
        vault_document["grace_period_event_published_at"] = None
        return vault_document

    if current_status != "grace_period":
        started_at = datetime.now(timezone.utc)
        expires_at = started_at + timedelta(days=grace_period_days)
        vault_document["status"] = "grace_period"
        vault_document["grace_period_started_at"] = started_at.isoformat()
        vault_document["grace_period_expires_at"] = expires_at.isoformat()
        vault_document["grace_period_event_published_at"] = None

    return vault_document


class LocalCosmosService:
    def __init__(self, data_file: Optional[str] = None) -> None:
        default_path = Path(__file__).resolve().parents[1] / ".local_data" / "vaults.json"
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

    @staticmethod
    def _is_vault_document(item: Dict[str, Any]) -> bool:
        doc_type = str(item.get("doc_type", "vault")).strip().lower()
        return doc_type == "vault"

    @staticmethod
    def _audit_partition_key(vault_id: Optional[str], owner_user_id: str) -> str:
        normalized_vault_id = str(vault_id or "").strip()
        if normalized_vault_id:
            return f"vault:{normalized_vault_id}"
        return f"user:{owner_user_id}"

    def create_user(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        items = self._read_items()
        payload = dict(user_data)
        payload["id"] = str(payload.get("id") or uuid4())
        payload["user_id"] = str(payload.get("user_id") or payload["id"])
        payload["doc_type"] = "user"

        email = str(payload.get("email", "")).strip().lower()
        if not email:
            raise ValueError("user_data must include email.")
        if self.get_user_by_email(email) is not None:
            raise ValueError("An account with this email already exists.")

        payload["email"] = email
        items.append(payload)
        self._write_items(items)
        return payload

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        normalized_email = email.strip().lower()
        items = self._read_items()
        return next(
            (
                item
                for item in items
                if str(item.get("doc_type", "")).strip().lower() == "user"
                and str(item.get("email", "")).strip().lower() == normalized_email
            ),
            None,
        )

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        normalized_username = username.strip().lower()
        items = self._read_items()
        return next(
            (
                item
                for item in items
                if str(item.get("doc_type", "")).strip().lower() == "user"
                and str(item.get("username", "")).strip().lower() == normalized_username
            ),
            None,
        )

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        items = self._read_items()
        return next(
            (
                item
                for item in items
                if str(item.get("doc_type", "")).strip().lower() == "user"
                and str(item.get("id")) == user_id
            ),
            None,
        )

    def get_user_by_verification_token_hash(self, token_hash: str) -> Optional[Dict[str, Any]]:
        items = self._read_items()
        return next(
            (
                item
                for item in items
                if str(item.get("doc_type", "")).strip().lower() == "user"
                and str(item.get("verification_token_hash", "")) == token_hash
            ),
            None,
        )

    def update_user(self, user_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        items = self._read_items()

        for index, item in enumerate(items):
            if str(item.get("doc_type", "")).strip().lower() != "user":
                continue
            if str(item.get("id")) != user_id:
                continue

            updated_item = dict(item)
            updated_item.update(update_data)
            updated_item["id"] = item["id"]
            updated_item["user_id"] = item["user_id"]
            updated_item["doc_type"] = "user"
            items[index] = updated_item
            self._write_items(items)
            return updated_item

        return None

    def create_vault(self, vault_data: Dict[str, Any]) -> Dict[str, Any]:
        if not vault_data.get("user_id"):
            raise ValueError("vault_data must include user_id.")

        items = self._read_items()
        payload = dict(vault_data)
        payload["id"] = str(payload.get("id") or uuid4())
        payload["doc_type"] = "vault"
        payload.setdefault("recipients", [])
        payload.setdefault("files", [])
        payload.setdefault("activation_requests", [])
        payload.setdefault("owner_message", None)
        items.append(payload)
        self._write_items(items)
        return payload

    def get_vault_by_id(self, vault_id: str) -> Optional[Dict[str, Any]]:
        items = self._read_items()
        return next(
            (
                item
                for item in items
                if self._is_vault_document(item) and str(item.get("id")) == vault_id
            ),
            None,
        )

    def get_vault_by_short_id(self, short_id: str) -> Optional[Dict[str, Any]]:
        items = self._read_items()
        return next(
            (
                item
                for item in items
                if self._is_vault_document(item)
                and str(item.get("short_id", "")).strip() == short_id
            ),
            None,
        )

    def list_vaults(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        items = [item for item in self._read_items() if self._is_vault_document(item)]
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

    def list_vaults_for_recipient(self, recipient_email: str) -> List[Dict[str, Any]]:
        normalized_email = recipient_email.strip().lower()
        items = [item for item in self._read_items() if self._is_vault_document(item)]
        matching: List[Dict[str, Any]] = []
        for item in items:
            recipients = item.get("recipients", [])
            if not isinstance(recipients, list):
                continue
            for recipient in recipients:
                if (
                    isinstance(recipient, str)
                    and recipient.strip().lower() == normalized_email
                ):
                    matching.append(item)
                    break
        return matching

    def add_activation_request(
        self,
        vault_id: str,
        recipient_email: str,
        reason: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        items = self._read_items()
        normalized_email = recipient_email.strip().lower()

        for index, item in enumerate(items):
            if not self._is_vault_document(item):
                continue
            if str(item.get("id")) != vault_id:
                continue

            recipients = item.get("recipients", [])
            if not isinstance(recipients, list):
                recipients = []

            recipient_is_known = any(
                isinstance(recipient, str)
                and recipient.strip().lower() == normalized_email
                for recipient in recipients
            )
            if not recipient_is_known:
                raise ValueError("Only configured recipients can request activation.")

            current_status = str(item.get("status", "active")).strip().lower()
            if current_status in VAULT_ACTIVATION_TERMINAL_STATUSES:
                return item, "terminal"

            activation_requests = item.get("activation_requests", [])
            if not isinstance(activation_requests, list):
                activation_requests = []

            already_requested = any(
                isinstance(request_item, dict)
                and str(request_item.get("recipient_email", "")).strip().lower()
                == normalized_email
                for request_item in activation_requests
            )
            if already_requested:
                return item, "duplicate"

            activation_requests.append(
                {
                    "recipient_email": normalized_email,
                    "requested_at": _now_iso(),
                    "reason": reason.strip()
                    if isinstance(reason, str) and reason.strip()
                    else None,
                }
            )

            updated_item = dict(item)
            updated_item["activation_requests"] = activation_requests
            _recompute_activation_state(updated_item)
            items[index] = updated_item
            self._write_items(items)
            return updated_item, "added"

        return None, "not_found"

    def remove_activation_request(
        self,
        vault_id: str,
        recipient_email: str,
    ) -> Optional[Dict[str, Any]]:
        items = self._read_items()
        normalized_email = recipient_email.strip().lower()

        for index, item in enumerate(items):
            if not self._is_vault_document(item):
                continue
            if str(item.get("id")) != vault_id:
                continue

            activation_requests = item.get("activation_requests", [])
            if not isinstance(activation_requests, list):
                activation_requests = []

            filtered_requests = [
                request_item
                for request_item in activation_requests
                if not (
                    isinstance(request_item, dict)
                    and str(request_item.get("recipient_email", "")).strip().lower()
                    == normalized_email
                )
            ]

            if len(filtered_requests) == len(activation_requests):
                return item

            updated_item = dict(item)
            updated_item["activation_requests"] = filtered_requests
            _recompute_activation_state(updated_item)
            items[index] = updated_item
            self._write_items(items)
            return updated_item

        return None

    def check_in_vault(self, vault_id: str) -> Optional[Dict[str, Any]]:
        items = self._read_items()

        for index, item in enumerate(items):
            if not self._is_vault_document(item):
                continue
            if str(item.get("id")) != vault_id:
                continue

            current_status = str(item.get("status", "active")).strip().lower()
            if current_status in VAULT_ACTIVATION_TERMINAL_STATUSES:
                raise ValueError("This vault can no longer be checked in.")

            updated_item = dict(item)
            updated_item["activation_requests"] = []
            updated_item["grace_period_started_at"] = None
            updated_item["grace_period_expires_at"] = None
            updated_item["grace_period_event_published_at"] = None
            updated_item["delivery_error"] = None
            updated_item["status"] = "active"
            updated_item["last_check_in_at"] = _now_iso()
            items[index] = updated_item
            self._write_items(items)
            return updated_item

        return None

    def delete_user(self, user_id: str) -> bool:
        items = self._read_items()
        remaining = [
            item
            for item in items
            if not (
                str(item.get("doc_type", "")).strip().lower() == "user"
                and str(item.get("id")) == user_id
            )
        ]
        if len(remaining) == len(items):
            return False

        self._write_items(remaining)
        return True

    def log_audit_event(
        self,
        *,
        event_type: str,
        owner_user_id: str,
        vault_id: Optional[str] = None,
        actor_user_id: Optional[str] = None,
        actor_email: Optional[str] = None,
        source: str = "api",
        metadata: Optional[Dict[str, Any]] = None,
        event_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        items = self._read_items()
        audit_item = {
            "id": str(uuid4()),
            "doc_type": "audit_log",
            "partition_key": self._audit_partition_key(vault_id, owner_user_id),
            "event_type": str(event_type).strip(),
            "event_at": event_at or _now_iso(),
            "owner_user_id": str(owner_user_id).strip(),
            "vault_id": str(vault_id).strip() if vault_id is not None else None,
            "actor_user_id": str(actor_user_id).strip() if actor_user_id else None,
            "actor_email": str(actor_email).strip().lower() if actor_email else None,
            "source": str(source).strip() or "api",
            "metadata": metadata or {},
        }
        items.append(audit_item)
        self._write_items(items)
        return audit_item

    def list_vault_audit_events(
        self,
        *,
        vault_id: str,
        owner_user_id: str,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        normalized_limit = max(1, min(int(limit), 500))
        partition_key = self._audit_partition_key(vault_id, owner_user_id)
        items = [
            item
            for item in self._read_items()
            if str(item.get("doc_type", "")).strip().lower() == "audit_log"
            and str(item.get("partition_key", "")) == partition_key
            and str(item.get("owner_user_id", "")) == owner_user_id
            and str(item.get("vault_id", "")) == vault_id
        ]
        items.sort(key=lambda item: str(item.get("event_at", "")), reverse=True)
        return items[:normalized_limit]
