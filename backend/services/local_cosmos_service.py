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


def _recipient_email(recipient: Any) -> str:
    if isinstance(recipient, dict):
        return str(recipient.get("email", "")).strip().lower()
    if isinstance(recipient, str):
        return recipient.strip().lower()
    return ""


def _recipient_can_activate(recipient: Any) -> bool:
    if isinstance(recipient, dict):
        return bool(recipient.get("can_activate", True))
    if isinstance(recipient, str):
        return True
    return False


def _normalize_recipients(recipients: Any) -> List[Dict[str, Any]]:
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
                "can_activate": _recipient_can_activate(recipient),
            }
        )
    return normalized_recipients


def _count_activatable_recipients(recipients: Any) -> int:
    return sum(
        1
        for recipient in _normalize_recipients(recipients)
        if _recipient_can_activate(recipient)
    )


def _clamp_activation_threshold(vault_document: Dict[str, Any]) -> Dict[str, Any]:
    current_threshold_raw = vault_document.get("activation_threshold", 1)
    try:
        current_threshold = max(1, int(current_threshold_raw))
    except (TypeError, ValueError):
        current_threshold = 1

    activatable_count = _count_activatable_recipients(vault_document.get("recipients", []))
    if activatable_count > 0:
        vault_document["activation_threshold"] = min(current_threshold, activatable_count)
    else:
        vault_document["activation_threshold"] = current_threshold
    return vault_document


def _prune_activation_requests(vault_document: Dict[str, Any]) -> Dict[str, Any]:
    activation_requests = vault_document.get("activation_requests", [])
    if not isinstance(activation_requests, list):
        activation_requests = []

    allowed_emails = {
        _recipient_email(recipient)
        for recipient in _normalize_recipients(vault_document.get("recipients", []))
        if _recipient_can_activate(recipient)
    }
    vault_document["activation_requests"] = [
        activation_request
        for activation_request in activation_requests
        if isinstance(activation_request, dict)
        and str(activation_request.get("recipient_email", "")).strip().lower() in allowed_emails
    ]
    return vault_document


def _normalize_files_for_recipients(vault_document: Dict[str, Any]) -> Dict[str, Any]:
    files = vault_document.get("files", [])
    if not isinstance(files, list):
        files = []

    available_emails = {
        _recipient_email(recipient)
        for recipient in _normalize_recipients(vault_document.get("recipients", []))
    }
    normalized_files: List[Dict[str, Any]] = []
    for file_item in files:
        if not isinstance(file_item, dict):
            continue
        normalized_file = dict(file_item)
        recipient_emails = normalized_file.get("recipient_emails")
        if isinstance(recipient_emails, list):
            normalized_file["recipient_emails"] = [
                str(recipient_email).strip().lower()
                for recipient_email in recipient_emails
                if str(recipient_email).strip().lower() in available_emails
            ]
        else:
            normalized_file["recipient_emails"] = sorted(available_emails)
        normalized_files.append(normalized_file)
    vault_document["files"] = normalized_files
    return vault_document


def _resolve_grace_period_hours(vault_document: Dict[str, Any]) -> int:
    raw_unit = str(vault_document.get("grace_period_unit", "")).strip().lower()
    raw_value = vault_document.get("grace_period_value")
    raw_hours = vault_document.get("grace_period_hours")
    raw_days = vault_document.get("grace_period_days", 0)

    try:
        if raw_value is not None and raw_unit in {"days", "hours"}:
            value = max(1, int(raw_value))
            return value * 24 if raw_unit == "days" else value
        if raw_hours is not None:
            return max(1, int(raw_hours))
        return max(0, int(raw_days)) * 24
    except (TypeError, ValueError):
        return 0


def _recompute_activation_state(vault_document: Dict[str, Any]) -> Dict[str, Any]:
    current_status = str(vault_document.get("status", "active")).strip().lower()
    if current_status in VAULT_ACTIVATION_TERMINAL_STATUSES:
        return vault_document

    vault_document["recipients"] = _normalize_recipients(vault_document.get("recipients", []))
    _normalize_files_for_recipients(vault_document)
    _clamp_activation_threshold(vault_document)
    _prune_activation_requests(vault_document)

    activation_requests = vault_document.get("activation_requests", [])
    if not isinstance(activation_requests, list):
        activation_requests = []

    requests_count = len(activation_requests)
    try:
        threshold = max(1, int(vault_document.get("activation_threshold", 1)))
    except (TypeError, ValueError):
        threshold = 1

    grace_period_hours = _resolve_grace_period_hours(vault_document)

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
        expires_at = started_at + timedelta(hours=grace_period_hours)
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
        payload["recipients"] = _normalize_recipients(payload.get("recipients", []))
        payload.setdefault("files", [])
        _normalize_files_for_recipients(payload)
        payload.setdefault("activation_requests", [])
        payload.setdefault("owner_message", None)
        payload.setdefault("delivery_packages", [])
        _clamp_activation_threshold(payload)
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

    def add_recipient_to_vault(self, vault_id: str, email: str, *, can_activate: bool = True) -> Optional[Dict[str, Any]]:
        items = self._read_items()
        normalized_email = email.strip().lower()

        for item in items:
            if str(item.get("id")) != vault_id:
                continue

            recipients = _normalize_recipients(item.get("recipients", []))

            if not any(_recipient_email(recipient) == normalized_email for recipient in recipients):
                recipients.append({"email": normalized_email, "can_activate": can_activate})

            item["recipients"] = recipients
            _normalize_files_for_recipients(item)
            _clamp_activation_threshold(item)
            _prune_activation_requests(item)
            _recompute_activation_state(item)
            self._write_items(items)
            return item

        return None

    def remove_recipient_from_vault(self, vault_id: str, email: str) -> Optional[Dict[str, Any]]:
        items = self._read_items()
        normalized_email = email.strip().lower()

        for item in items:
            if str(item.get("id")) != vault_id:
                continue

            recipients = _normalize_recipients(item.get("recipients", []))

            item["recipients"] = [
                recipient
                for recipient in recipients
                if _recipient_email(recipient) != normalized_email
            ]
            _normalize_files_for_recipients(item)
            _clamp_activation_threshold(item)
            _prune_activation_requests(item)
            _recompute_activation_state(item)
            self._write_items(items)
            return item

        return None

    def update_recipient_activation_permission(
        self,
        vault_id: str,
        email: str,
        *,
        can_activate: bool,
    ) -> Optional[Dict[str, Any]]:
        items = self._read_items()
        normalized_email = email.strip().lower()

        for item in items:
            if str(item.get("id")) != vault_id:
                continue

            recipients = _normalize_recipients(item.get("recipients", []))
            recipient_found = False
            updated_recipients: List[Dict[str, Any]] = []
            for recipient in recipients:
                if _recipient_email(recipient) == normalized_email:
                    recipient_found = True
                    updated_recipients.append({"email": normalized_email, "can_activate": can_activate})
                else:
                    updated_recipients.append(recipient)

            if not recipient_found:
                raise ValueError("Recipient not found in vault.")

            item["recipients"] = updated_recipients
            _normalize_files_for_recipients(item)
            _clamp_activation_threshold(item)
            _prune_activation_requests(item)
            _recompute_activation_state(item)
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
            updated_item["recipients"] = _normalize_recipients(updated_item.get("recipients", []))
            _normalize_files_for_recipients(updated_item)
            _clamp_activation_threshold(updated_item)
            _prune_activation_requests(updated_item)
            _recompute_activation_state(updated_item)
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
                if _recipient_email(recipient) == normalized_email:
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

            recipients = _normalize_recipients(item.get("recipients", []))
            matching_recipient = next(
                (recipient for recipient in recipients if _recipient_email(recipient) == normalized_email),
                None,
            )
            if matching_recipient is None:
                raise ValueError("Only configured recipients can request activation.")
            if not _recipient_can_activate(matching_recipient):
                raise ValueError("This recipient is not allowed to activate the vault.")

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
