from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from azure.cosmos import CosmosClient, PartitionKey, exceptions

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        logger.info(
            "Audit Cosmos container initialized. database=%s container=%s",
            self._database_name,
            self._container_name,
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
        actor_user_id: Optional[str] = None,
        actor_email: Optional[str] = None,
        actor_type: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        occurred_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        container = self._get_container()
        payload = {
            "id": str(uuid4()),
            "doc_type": "audit_event",
            "owner_user_id": owner_user_id,
            "vault_id": vault_id,
            "event_type": event_type,
            "occurred_at": occurred_at or _now_iso(),
            "actor_user_id": actor_user_id,
            "actor_email": actor_email,
            "actor_type": actor_type,
            "details": details or {},
        }
        try:
            return container.create_item(body=payload)
        except exceptions.CosmosHttpResponseError:
            logger.exception(
                "Failed to persist audit event. owner_user_id=%s event_type=%s vault_id=%s",
                owner_user_id,
                event_type,
                vault_id,
            )
            raise

    def list_events_for_vault(self, *, owner_user_id: str, vault_id: str) -> List[Dict[str, Any]]:
        container = self._get_container()
        query = """
        SELECT * FROM c
        WHERE c.doc_type = 'audit_event'
          AND c.owner_user_id = @owner_user_id
          AND (c.vault_id = @vault_id OR (IS_NULL(c.vault_id) AND c.event_type = 'login'))
        ORDER BY c.occurred_at DESC
        """
        parameters = [
            {"name": "@owner_user_id", "value": owner_user_id},
            {"name": "@vault_id", "value": vault_id},
        ]
        return list(
            container.query_items(
                query=query,
                parameters=parameters,
                partition_key=owner_user_id,
            )
        )


class LocalAuditService:
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

    def record_event(
        self,
        *,
        owner_user_id: str,
        event_type: str,
        vault_id: Optional[str] = None,
        actor_user_id: Optional[str] = None,
        actor_email: Optional[str] = None,
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
            "occurred_at": occurred_at or _now_iso(),
            "actor_user_id": actor_user_id,
            "actor_email": actor_email,
            "actor_type": actor_type,
            "details": details or {},
        }
        items.append(payload)
        self._write_items(items)
        return payload

    def list_events_for_vault(self, *, owner_user_id: str, vault_id: str) -> List[Dict[str, Any]]:
        items = self._read_items()
        matching = [
            item
            for item in items
            if str(item.get("doc_type", "")).strip().lower() == "audit_event"
            and str(item.get("owner_user_id", "")) == owner_user_id
            and (
                str(item.get("vault_id", "")) == vault_id
                or (
                    item.get("vault_id") is None
                    and str(item.get("event_type", "")) == "login"
                )
            )
        ]
        return sorted(matching, key=lambda item: str(item.get("occurred_at", "")), reverse=True)
