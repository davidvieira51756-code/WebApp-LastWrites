from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from azure.cosmos import CosmosClient, PartitionKey, exceptions

logger = logging.getLogger(__name__)


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
    """Given a vault document, recompute status / grace period based on activation requests.

    Rules:
    - If current status is terminal (delivery_initiated, delivered, delivered_archived, disabled), do not mutate.
    - If requests count >= threshold and status not already grace_period, transition to grace_period
      and set grace_period_started_at / grace_period_expires_at.
    - If requests count > 0 and < threshold, status becomes pending_activation and any grace period
      markers are cleared.
    - If requests count == 0, status returns to active and grace period markers are cleared.
    """

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
    threshold_raw = vault_document.get("activation_threshold", 1)
    try:
        threshold = max(1, int(threshold_raw))
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

    # Threshold reached or exceeded.
    if current_status != "grace_period":
        started_at = datetime.now(timezone.utc)
        expires_at = started_at + timedelta(hours=grace_period_hours)
        vault_document["status"] = "grace_period"
        vault_document["grace_period_started_at"] = started_at.isoformat()
        vault_document["grace_period_expires_at"] = expires_at.isoformat()
        vault_document["grace_period_event_published_at"] = None

    return vault_document


class CosmosService:
    def __init__(self, connection_string: Optional[str] = None) -> None:
        self._connection_string = connection_string or os.getenv("COSMOS_CONNECTION_STRING")
        if not self._connection_string:
            raise ValueError(
                "Environment variable COSMOS_CONNECTION_STRING is required."
            )

        self._database_name = os.getenv("COSMOS_DATABASE_NAME", "last-writes-db")
        self._container_name = os.getenv("COSMOS_VAULTS_CONTAINER", "vaults")
        self._audit_container_name = os.getenv("COSMOS_AUDIT_CONTAINER", "audit_logs")
        throughput_value = os.getenv("COSMOS_CONTAINER_THROUGHPUT", "400")
        self._container_throughput = int(throughput_value)

        self._client: Optional[CosmosClient] = None
        self._database = None
        self._container = None
        self._audit_container = None

    def initialize(self) -> None:
        try:
            self._client = CosmosClient.from_connection_string(self._connection_string)
            self._database = self._client.create_database_if_not_exists(
                id=self._database_name
            )
            self._container = self._database.create_container_if_not_exists(
                id=self._container_name,
                partition_key=PartitionKey(path="/user_id"),
                offer_throughput=self._container_throughput,
            )
            self._audit_container = self._database.create_container_if_not_exists(
                id=self._audit_container_name,
                partition_key=PartitionKey(path="/partition_key"),
                offer_throughput=self._container_throughput,
            )
            logger.info(
                "Cosmos DB initialized. database=%s container=%s audit_container=%s",
                self._database_name,
                self._container_name,
                self._audit_container_name,
            )
        except Exception:
            logger.exception("Failed to initialize Cosmos DB resources.")
            raise

    def _get_container(self):
        if self._container is None:
            raise RuntimeError("CosmosService is not initialized.")
        return self._container

    def _get_audit_container(self):
        if self._audit_container is None:
            raise RuntimeError("CosmosService is not initialized.")
        return self._audit_container

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
        container = self._get_container()
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

        try:
            created_item = container.create_item(body=payload)
            logger.info("Created user id=%s", created_item.get("id"))
            return created_item
        except exceptions.CosmosHttpResponseError:
            logger.exception("Cosmos DB create_item failed for user email=%s", email)
            raise

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        container = self._get_container()
        normalized_email = email.strip().lower()
        query = (
            "SELECT * FROM c WHERE c.doc_type = 'user' "
            "AND LOWER(c.email) = @email"
        )
        parameters = [{"name": "@email", "value": normalized_email}]

        try:
            items = list(
                container.query_items(
                    query=query,
                    parameters=parameters,
                    enable_cross_partition_query=True,
                )
            )
        except exceptions.CosmosHttpResponseError:
            logger.exception("Cosmos DB read failed for user email=%s", normalized_email)
            raise

        if not items:
            return None
        return items[0]

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        container = self._get_container()
        normalized_username = username.strip().lower()
        query = (
            "SELECT * FROM c WHERE c.doc_type = 'user' "
            "AND LOWER(c.username) = @username"
        )
        parameters = [{"name": "@username", "value": normalized_username}]

        try:
            items = list(
                container.query_items(
                    query=query,
                    parameters=parameters,
                    enable_cross_partition_query=True,
                )
            )
        except exceptions.CosmosHttpResponseError:
            logger.exception("Cosmos DB read failed for username=%s", normalized_username)
            raise

        if not items:
            return None
        return items[0]

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        container = self._get_container()
        query = "SELECT * FROM c WHERE c.doc_type = 'user' AND c.id = @id"
        parameters = [{"name": "@id", "value": user_id}]

        try:
            items = list(
                container.query_items(
                    query=query,
                    parameters=parameters,
                    enable_cross_partition_query=True,
                )
            )
        except exceptions.CosmosHttpResponseError:
            logger.exception("Cosmos DB read failed for user id=%s", user_id)
            raise

        if not items:
            return None
        return items[0]

    def get_user_by_verification_token_hash(self, token_hash: str) -> Optional[Dict[str, Any]]:
        container = self._get_container()
        query = (
            "SELECT * FROM c WHERE c.doc_type = 'user' "
            "AND c.verification_token_hash = @token_hash"
        )
        parameters = [{"name": "@token_hash", "value": token_hash}]

        try:
            items = list(
                container.query_items(
                    query=query,
                    parameters=parameters,
                    enable_cross_partition_query=True,
                )
            )
        except exceptions.CosmosHttpResponseError:
            logger.exception("Cosmos DB token lookup failed for email verification.")
            raise

        if not items:
            return None
        return items[0]

    def update_user(self, user_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        container = self._get_container()
        existing_item = self.get_user_by_id(user_id)
        if existing_item is None:
            return None

        merged_item = dict(existing_item)
        merged_item.update(update_data)
        merged_item["id"] = existing_item["id"]
        merged_item["user_id"] = existing_item["user_id"]
        merged_item["doc_type"] = "user"

        try:
            updated_item = container.replace_item(item=existing_item, body=merged_item)
            logger.info("Updated user id=%s", existing_item["id"])
            return updated_item
        except exceptions.CosmosHttpResponseError:
            logger.exception("Cosmos DB update failed for user id=%s", user_id)
            raise

    def delete_user(self, user_id: str) -> bool:
        container = self._get_container()
        existing_item = self.get_user_by_id(user_id)
        if existing_item is None:
            return False

        try:
            container.delete_item(
                item=existing_item["id"],
                partition_key=existing_item["user_id"],
            )
            logger.info("Deleted user id=%s", existing_item["id"])
            return True
        except exceptions.CosmosHttpResponseError:
            logger.exception("Cosmos DB delete failed for user id=%s", user_id)
            raise

    def create_vault(self, vault_data: Dict[str, Any]) -> Dict[str, Any]:
        container = self._get_container()
        payload = dict(vault_data)
        payload["id"] = payload.get("id") or str(uuid4())
        payload["doc_type"] = "vault"
        payload["recipients"] = _normalize_recipients(payload.get("recipients", []))
        payload.setdefault("files", [])
        _normalize_files_for_recipients(payload)
        payload.setdefault("activation_requests", [])
        payload.setdefault("owner_message", None)
        payload.setdefault("delivery_packages", [])
        _clamp_activation_threshold(payload)

        if not payload.get("user_id"):
            raise ValueError("vault_data must include user_id.")

        try:
            created_item = container.create_item(body=payload)
            logger.info(
                "Created vault id=%s user_id=%s",
                created_item.get("id"),
                created_item.get("user_id"),
            )
            return created_item
        except exceptions.CosmosHttpResponseError:
            logger.exception("Cosmos DB create_item failed for vault.")
            raise

    def get_vault_by_id(self, vault_id: str) -> Optional[Dict[str, Any]]:
        container = self._get_container()
        query = "SELECT * FROM c WHERE c.id = @id AND c.doc_type = 'vault'"
        parameters = [{"name": "@id", "value": vault_id}]

        try:
            items = list(
                container.query_items(
                    query=query,
                    parameters=parameters,
                    enable_cross_partition_query=True,
                )
            )
            if not items:
                return None
            return items[0]
        except exceptions.CosmosHttpResponseError:
            logger.exception("Cosmos DB read failed for vault id=%s", vault_id)
            raise

    def get_vault_by_short_id(self, short_id: str) -> Optional[Dict[str, Any]]:
        container = self._get_container()
        query = "SELECT * FROM c WHERE c.short_id = @short_id AND c.doc_type = 'vault'"
        parameters = [{"name": "@short_id", "value": short_id}]

        try:
            items = list(
                container.query_items(
                    query=query,
                    parameters=parameters,
                    enable_cross_partition_query=True,
                )
            )
            if not items:
                return None
            return items[0]
        except exceptions.CosmosHttpResponseError:
            logger.exception("Cosmos DB read failed for vault short_id=%s", short_id)
            raise

    def list_vaults(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        container = self._get_container()
        query = "SELECT * FROM c WHERE c.doc_type = 'vault'"
        parameters = None

        if user_id:
            query = "SELECT * FROM c WHERE c.doc_type = 'vault' AND c.user_id = @user_id"
            parameters = [{"name": "@user_id", "value": user_id}]

        try:
            items = list(
                container.query_items(
                    query=query,
                    parameters=parameters,
                    enable_cross_partition_query=True,
                )
            )
            return [item for item in items if self._is_vault_document(item)]
        except exceptions.CosmosHttpResponseError:
            logger.exception("Cosmos DB list query failed.")
            raise

    def add_recipient_to_vault(
        self,
        vault_id: str,
        email: str,
        *,
        can_activate: bool = True,
    ) -> Optional[Dict[str, Any]]:
        container = self._get_container()
        existing_item = self.get_vault_by_id(vault_id)
        if existing_item is None:
            return None

        user_id = existing_item.get("user_id")
        if not user_id:
            raise ValueError(
                f"Vault document is missing user_id partition key. vault_id={vault_id}"
            )

        recipients = _normalize_recipients(existing_item.get("recipients", []))

        normalized_email = email.strip().lower()
        email_exists = any(_recipient_email(recipient) == normalized_email for recipient in recipients)

        if not email_exists:
            recipients.append({"email": normalized_email, "can_activate": can_activate})
            logger.info("Added recipient to vault. vault_id=%s email=%s", vault_id, email)
        else:
            logger.info(
                "Recipient already exists in vault. vault_id=%s email=%s",
                vault_id,
                email,
            )

        updated_item = dict(existing_item)
        updated_item["recipients"] = recipients
        _normalize_files_for_recipients(updated_item)
        _clamp_activation_threshold(updated_item)
        _prune_activation_requests(updated_item)
        _recompute_activation_state(updated_item)

        try:
            saved_item = container.replace_item(
                item=existing_item,
                body=updated_item,
            )
            return saved_item
        except exceptions.CosmosHttpResponseError:
            logger.exception(
                "Cosmos DB recipient update failed for vault id=%s",
                vault_id,
            )
            raise

    def remove_recipient_from_vault(
        self,
        vault_id: str,
        email: str,
    ) -> Optional[Dict[str, Any]]:
        container = self._get_container()
        existing_item = self.get_vault_by_id(vault_id)
        if existing_item is None:
            return None

        user_id = existing_item.get("user_id")
        if not user_id:
            raise ValueError(
                f"Vault document is missing user_id partition key. vault_id={vault_id}"
            )

        recipients = _normalize_recipients(existing_item.get("recipients", []))

        normalized_email = email.strip().lower()
        updated_recipients = [
            recipient
            for recipient in recipients
            if _recipient_email(recipient) != normalized_email
        ]

        updated_item = dict(existing_item)
        updated_item["recipients"] = updated_recipients
        _normalize_files_for_recipients(updated_item)
        _clamp_activation_threshold(updated_item)
        _prune_activation_requests(updated_item)
        _recompute_activation_state(updated_item)

        try:
            saved_item = container.replace_item(
                item=existing_item,
                body=updated_item,
            )
            return saved_item
        except exceptions.CosmosHttpResponseError:
            logger.exception(
                "Cosmos DB recipient removal failed for vault id=%s",
                vault_id,
            )
            raise

    def get_vault_files(self, vault_id: str) -> Optional[List[Dict[str, Any]]]:
        existing_item = self.get_vault_by_id(vault_id)
        if existing_item is None:
            return None

        raw_files = existing_item.get("files", [])
        if not isinstance(raw_files, list):
            return []

        files = [file_item for file_item in raw_files if isinstance(file_item, dict)]
        return files

    def remove_file_from_vault(self, vault_id: str, file_id: str) -> Optional[Dict[str, Any]]:
        container = self._get_container()
        existing_item = self.get_vault_by_id(vault_id)
        if existing_item is None:
            return None

        user_id = existing_item.get("user_id")
        if not user_id:
            raise ValueError(
                f"Vault document is missing user_id partition key. vault_id={vault_id}"
            )

        existing_files = existing_item.get("files", [])
        if not isinstance(existing_files, list):
            existing_files = []

        updated_files = [
            file_item
            for file_item in existing_files
            if not (
                isinstance(file_item, dict)
                and str(file_item.get("id")) == file_id
            )
        ]

        updated_item = dict(existing_item)
        updated_item["files"] = updated_files

        try:
            saved_item = container.replace_item(
                item=existing_item,
                body=updated_item,
            )
            return saved_item
        except exceptions.CosmosHttpResponseError:
            logger.exception(
                "Cosmos DB file removal failed for vault id=%s",
                vault_id,
            )
            raise

    def update_vault(
        self, vault_id: str, update_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        container = self._get_container()
        existing_item = self.get_vault_by_id(vault_id)
        if existing_item is None:
            return None

        merged_item = dict(existing_item)
        merged_item.update(update_data)
        merged_item["id"] = existing_item["id"]
        merged_item["user_id"] = existing_item["user_id"]
        merged_item["recipients"] = _normalize_recipients(merged_item.get("recipients", []))
        _normalize_files_for_recipients(merged_item)
        _clamp_activation_threshold(merged_item)
        _prune_activation_requests(merged_item)
        _recompute_activation_state(merged_item)

        try:
            updated_item = container.replace_item(
                item=existing_item,
                body=merged_item,
            )
            logger.info("Updated vault id=%s", existing_item["id"])
            return updated_item
        except exceptions.CosmosHttpResponseError:
            logger.exception("Cosmos DB update failed for vault id=%s", vault_id)
            raise

    def delete_vault(self, vault_id: str) -> bool:
        container = self._get_container()
        existing_item = self.get_vault_by_id(vault_id)
        if existing_item is None:
            return False

        try:
            container.delete_item(
                item=existing_item["id"],
                partition_key=existing_item["user_id"],
            )
            logger.info("Deleted vault id=%s", existing_item["id"])
            return True
        except exceptions.CosmosHttpResponseError:
            logger.exception("Cosmos DB delete failed for vault id=%s", vault_id)
            raise

    def list_vaults_for_recipient(self, recipient_email: str) -> List[Dict[str, Any]]:
        container = self._get_container()
        normalized_email = recipient_email.strip().lower()
        query = "SELECT * FROM c WHERE c.doc_type = 'vault'"

        try:
            items = list(
                container.query_items(
                    query=query,
                    enable_cross_partition_query=True,
                )
            )
            matching_items: List[Dict[str, Any]] = []
            for item in items:
                if not self._is_vault_document(item):
                    continue
                recipients = item.get("recipients", [])
                if isinstance(recipients, list) and any(
                    _recipient_email(recipient) == normalized_email
                    for recipient in recipients
                ):
                    matching_items.append(item)
            return matching_items
        except exceptions.CosmosHttpResponseError:
            logger.exception(
                "Cosmos DB list-for-recipient query failed for email=%s",
                normalized_email,
            )
            raise

    def add_activation_request(
        self,
        vault_id: str,
        recipient_email: str,
        reason: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        """Record an activation request from a recipient.

        Returns (updated_vault, outcome) where outcome is one of:
          - "added": request inserted
          - "duplicate": same recipient had already requested
          - "terminal": vault is in a terminal status and cannot accept new requests
        """

        container = self._get_container()
        existing_item = self.get_vault_by_id(vault_id)
        if existing_item is None:
            return None, "not_found"

        normalized_email = recipient_email.strip().lower()
        recipients = _normalize_recipients(existing_item.get("recipients", []))

        matching_recipient = next(
            (recipient for recipient in recipients if _recipient_email(recipient) == normalized_email),
            None,
        )
        if matching_recipient is None:
            raise ValueError("Only configured recipients can request activation.")
        if not _recipient_can_activate(matching_recipient):
            raise ValueError("This recipient is not allowed to activate the vault.")

        current_status = str(existing_item.get("status", "active")).strip().lower()
        if current_status in VAULT_ACTIVATION_TERMINAL_STATUSES:
            return existing_item, "terminal"

        activation_requests = existing_item.get("activation_requests", [])
        if not isinstance(activation_requests, list):
            activation_requests = []

        already_requested = any(
            isinstance(request_item, dict)
            and str(request_item.get("recipient_email", "")).strip().lower()
            == normalized_email
            for request_item in activation_requests
        )
        if already_requested:
            return existing_item, "duplicate"

        activation_requests.append(
            {
                "recipient_email": normalized_email,
                "requested_at": _now_iso(),
                "reason": reason.strip() if isinstance(reason, str) and reason.strip() else None,
            }
        )

        updated_item = dict(existing_item)
        updated_item["activation_requests"] = activation_requests
        _recompute_activation_state(updated_item)

        try:
            saved_item = container.replace_item(item=existing_item, body=updated_item)
            logger.info(
                "Activation request recorded. vault_id=%s recipient=%s new_status=%s",
                vault_id,
                normalized_email,
                saved_item.get("status"),
            )
            return saved_item, "added"
        except exceptions.CosmosHttpResponseError:
            logger.exception(
                "Cosmos DB activation request update failed vault_id=%s",
                vault_id,
            )
            raise

    def remove_activation_request(
        self,
        vault_id: str,
        recipient_email: str,
    ) -> Optional[Dict[str, Any]]:
        container = self._get_container()
        existing_item = self.get_vault_by_id(vault_id)
        if existing_item is None:
            return None

        normalized_email = recipient_email.strip().lower()
        activation_requests = existing_item.get("activation_requests", [])
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
            return existing_item

        updated_item = dict(existing_item)
        updated_item["activation_requests"] = filtered_requests
        _recompute_activation_state(updated_item)

        try:
            saved_item = container.replace_item(item=existing_item, body=updated_item)
            logger.info(
                "Activation request withdrawn. vault_id=%s recipient=%s new_status=%s",
                vault_id,
                normalized_email,
                saved_item.get("status"),
            )
            return saved_item
        except exceptions.CosmosHttpResponseError:
            logger.exception(
                "Cosmos DB activation withdrawal failed vault_id=%s",
                vault_id,
            )
            raise

    def check_in_vault(self, vault_id: str) -> Optional[Dict[str, Any]]:
        """Owner explicitly signals they are still alive. Clears requests and grace period."""

        container = self._get_container()
        existing_item = self.get_vault_by_id(vault_id)
        if existing_item is None:
            return None

        current_status = str(existing_item.get("status", "active")).strip().lower()
        if current_status in VAULT_ACTIVATION_TERMINAL_STATUSES:
            raise ValueError("This vault can no longer be checked in.")

        updated_item = dict(existing_item)
        updated_item["activation_requests"] = []
        updated_item["grace_period_started_at"] = None
        updated_item["grace_period_expires_at"] = None
        updated_item["grace_period_event_published_at"] = None
        updated_item["delivery_error"] = None
        updated_item["status"] = "active"
        updated_item["last_check_in_at"] = _now_iso()

        try:
            saved_item = container.replace_item(item=existing_item, body=updated_item)
            logger.info("Vault check-in recorded. vault_id=%s", vault_id)
            return saved_item
        except exceptions.CosmosHttpResponseError:
            logger.exception("Cosmos DB check-in update failed vault_id=%s", vault_id)
            raise

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
        container = self._get_audit_container()
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

        try:
            created_item = container.create_item(body=audit_item)
            logger.info(
                "Audit event recorded. event_type=%s vault_id=%s owner_user_id=%s",
                audit_item["event_type"],
                audit_item["vault_id"],
                audit_item["owner_user_id"],
            )
            return created_item
        except exceptions.CosmosHttpResponseError:
            logger.exception(
                "Cosmos DB audit log create failed. event_type=%s vault_id=%s owner_user_id=%s",
                audit_item["event_type"],
                audit_item["vault_id"],
                audit_item["owner_user_id"],
            )
            raise

    def update_recipient_activation_permission(
        self,
        vault_id: str,
        email: str,
        *,
        can_activate: bool,
    ) -> Optional[Dict[str, Any]]:
        container = self._get_container()
        existing_item = self.get_vault_by_id(vault_id)
        if existing_item is None:
            return None

        recipients = _normalize_recipients(existing_item.get("recipients", []))
        normalized_email = email.strip().lower()
        recipient_found = False
        updated_recipients: List[Dict[str, Any]] = []
        for recipient in recipients:
            if _recipient_email(recipient) == normalized_email:
                recipient_found = True
                updated_recipients.append(
                    {
                        "email": normalized_email,
                        "can_activate": can_activate,
                    }
                )
            else:
                updated_recipients.append(recipient)

        if not recipient_found:
            raise ValueError("Recipient not found in vault.")

        updated_item = dict(existing_item)
        updated_item["recipients"] = updated_recipients
        _normalize_files_for_recipients(updated_item)
        _clamp_activation_threshold(updated_item)
        _prune_activation_requests(updated_item)
        _recompute_activation_state(updated_item)

        try:
            saved_item = container.replace_item(
                item=existing_item,
                body=updated_item,
            )
            return saved_item
        except exceptions.CosmosHttpResponseError:
            logger.exception(
                "Cosmos DB recipient permission update failed for vault id=%s",
                vault_id,
            )
            raise

    def list_vault_audit_events(
        self,
        *,
        vault_id: str,
        owner_user_id: str,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        container = self._get_audit_container()
        normalized_limit = max(1, min(int(limit), 500))
        partition_key = self._audit_partition_key(vault_id, owner_user_id)
        query = (
            "SELECT * FROM c WHERE c.doc_type = 'audit_log' "
            "AND c.partition_key = @partition_key "
            "AND c.owner_user_id = @owner_user_id "
            "AND c.vault_id = @vault_id "
            "ORDER BY c.event_at DESC"
        )
        parameters = [
            {"name": "@partition_key", "value": partition_key},
            {"name": "@owner_user_id", "value": owner_user_id},
            {"name": "@vault_id", "value": vault_id},
        ]

        try:
            items = list(
                container.query_items(
                    query=query,
                    parameters=parameters,
                    partition_key=partition_key,
                )
            )
            return items[:normalized_limit]
        except exceptions.CosmosHttpResponseError:
            logger.exception(
                "Cosmos DB audit query failed. vault_id=%s owner_user_id=%s",
                vault_id,
                owner_user_id,
            )
            raise
