from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import azure.functions as func
from azure.cosmos import CosmosClient, exceptions

from shared_telemetry import configure_application_insights

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))
configure_application_insights("last-writes-process-events")

_cosmos_client: Optional[CosmosClient] = None
_vaults_container = None


def _extract_vault_id(event_data: Dict[str, Any], subject: str) -> Optional[str]:
    vault_id = event_data.get("vault_id") or event_data.get("vaultId")
    if vault_id:
        return str(vault_id)

    if "/vaults/" in subject:
        subject_parts = [part for part in subject.split("/") if part]
        if "vaults" in subject_parts:
            vault_index = subject_parts.index("vaults")
            if vault_index + 1 < len(subject_parts):
                return subject_parts[vault_index + 1]

    return None


def _get_vaults_container():
    global _cosmos_client
    global _vaults_container

    if _vaults_container is not None:
        return _vaults_container

    connection_string = os.getenv("COSMOS_CONNECTION_STRING")
    database_name = os.getenv("COSMOS_DATABASE_NAME", "last-writes-db")
    container_name = os.getenv("COSMOS_VAULTS_CONTAINER", "vaults")

    if not connection_string:
        raise RuntimeError("Environment variable COSMOS_CONNECTION_STRING is required.")

    _cosmos_client = CosmosClient.from_connection_string(connection_string)
    database_client = _cosmos_client.get_database_client(database_name)
    _vaults_container = database_client.get_container_client(container_name)

    logger.info(
        "Cosmos container client initialized for function processing. database=%s container=%s",
        database_name,
        container_name,
    )

    return _vaults_container


def _update_vault_status_to_delivery_initiated(
    vault_id: str,
    event_id: str,
    event_type: str,
) -> Optional[Dict[str, Any]]:
    container = _get_vaults_container()
    logger.info("Looking up vault document for vault_id=%s", vault_id)

    query = "SELECT * FROM c WHERE c.id = @vault_id"
    parameters = [{"name": "@vault_id", "value": vault_id}]

    try:
        items = list(
            container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True,
            )
        )
    except exceptions.CosmosHttpResponseError:
        logger.exception("Cosmos query failed for vault_id=%s", vault_id)
        raise

    if not items:
        logger.warning("No vault document found for vault_id=%s", vault_id)
        return None

    vault_document = items[0]
    previous_status = vault_document.get("status", "unknown")
    user_id = vault_document.get("user_id")
    if not user_id:
        raise ValueError(
            f"Vault document is missing user_id partition key. vault_id={vault_id}"
        )

    vault_document["status"] = "delivery_initiated"
    vault_document["delivery_initiated_at"] = datetime.now(timezone.utc).isoformat()
    vault_document["delivery_trigger"] = {
        "event_id": event_id,
        "event_type": event_type,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        updated_document = container.replace_item(
            item=vault_document,
            body=vault_document,
        )
        logger.info(
            "Vault status updated successfully. vault_id=%s previous_status=%s new_status=%s",
            vault_id,
            previous_status,
            updated_document.get("status"),
        )
        return updated_document
    except exceptions.CosmosHttpResponseError:
        logger.exception(
            "Cosmos replace_item failed while updating vault status. vault_id=%s",
            vault_id,
        )
        raise


def main(event: func.EventGridEvent) -> None:
    logger.info(
        "Event Grid event received. id=%s type=%s subject=%s topic=%s",
        event.id,
        event.event_type,
        event.subject,
        event.topic,
    )

    try:
        event_data = event.get_json()
    except ValueError:
        logger.exception("Event payload is not valid JSON. event_id=%s", event.id)
        return

    if not isinstance(event_data, dict):
        logger.warning(
            "Event payload is not an object; processing halted. event_id=%s payload_type=%s",
            event.id,
            type(event_data).__name__,
        )
        return

    logger.info("Event payload keys=%s", list(event_data.keys()))

    if not (
        event.event_type == "GracePeriodExpired"
        or event.event_type.endswith(".GracePeriodExpired")
    ):
        logger.warning(
            "Unsupported event type; skipping processing. event_id=%s event_type=%s",
            event.id,
            event.event_type,
        )
        return

    vault_id = _extract_vault_id(event_data=event_data, subject=event.subject or "")
    if not vault_id:
        logger.error(
            "vault_id was not found in event payload or subject. event_id=%s subject=%s",
            event.id,
            event.subject,
        )
        return

    logger.info("Starting processing pipeline for vault_id=%s", vault_id)

    try:
        updated_vault = _update_vault_status_to_delivery_initiated(
            vault_id=vault_id,
            event_id=event.id,
            event_type=event.event_type,
        )
        if updated_vault is None:
            logger.warning(
                "Vault not found for processing. vault_id=%s",
                vault_id,
            )
            return

        logger.info(
            "Grace period expiration event processed successfully. event_id=%s vault_id=%s",
            event.id,
            vault_id,
        )
    except Exception:
        logger.exception(
            "Unhandled error while processing event. event_id=%s vault_id=%s",
            event.id,
            vault_id,
        )
