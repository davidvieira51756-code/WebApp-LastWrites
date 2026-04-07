from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import azure.functions as func
from azure.core.credentials import AzureKeyCredential
from azure.cosmos import CosmosClient, exceptions
from azure.eventgrid import EventGridEvent, EventGridPublisherClient

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

_cosmos_client: Optional[CosmosClient] = None
_vaults_container = None
_event_grid_client: Optional[EventGridPublisherClient] = None


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def _get_last_activity_at(vault_document: Dict[str, Any]) -> Optional[datetime]:
    for field_name in ("last_login_at", "last_validation_at", "last_check_in_at"):
        parsed = _parse_iso_datetime(vault_document.get(field_name))
        if parsed is not None:
            return parsed

    return None


def _get_vaults_container():
    global _cosmos_client
    global _vaults_container

    if _vaults_container is not None:
        return _vaults_container

    connection_string = os.getenv("COSMOS_CONNECTION_STRING")
    if not connection_string:
        raise RuntimeError("Environment variable COSMOS_CONNECTION_STRING is required.")

    database_name = os.getenv("COSMOS_DATABASE_NAME", "last-writes-db")
    container_name = os.getenv("COSMOS_VAULTS_CONTAINER", "vaults")

    _cosmos_client = CosmosClient.from_connection_string(connection_string)
    database_client = _cosmos_client.get_database_client(database_name)
    _vaults_container = database_client.get_container_client(container_name)

    logger.info(
        "Cosmos client initialized. database=%s container=%s",
        database_name,
        container_name,
    )

    return _vaults_container


def _get_event_grid_client() -> EventGridPublisherClient:
    global _event_grid_client

    if _event_grid_client is not None:
        return _event_grid_client

    endpoint = os.getenv("EVENT_GRID_ENDPOINT")
    key = os.getenv("EVENT_GRID_KEY")

    if not endpoint:
        raise RuntimeError("Environment variable EVENT_GRID_ENDPOINT is required.")
    if not key:
        raise RuntimeError("Environment variable EVENT_GRID_KEY is required.")

    _event_grid_client = EventGridPublisherClient(
        endpoint=endpoint,
        credential=AzureKeyCredential(key),
    )

    logger.info("Event Grid publisher client initialized. endpoint=%s", endpoint)
    return _event_grid_client


def _query_candidate_vaults() -> List[Dict[str, Any]]:
    container = _get_vaults_container()
    query = """
    SELECT * FROM c
    WHERE c.status = @active_status
      AND c.grace_period_days > 0
      AND (
        IS_DEFINED(c.last_login_at)
        OR IS_DEFINED(c.last_validation_at)
        OR IS_DEFINED(c.last_check_in_at)
      )
      AND (
        NOT IS_DEFINED(c.grace_period_event_published_at)
        OR IS_NULL(c.grace_period_event_published_at)
      )
    """
    parameters = [{"name": "@active_status", "value": "active"}]

    try:
        vaults = list(
            container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True,
            )
        )
        logger.info("Candidate vault query completed. count=%s", len(vaults))
        return vaults
    except exceptions.CosmosHttpResponseError:
        logger.exception("Cosmos query failed while searching for expired vault candidates.")
        raise


def _mark_event_published(
    vault_document: Dict[str, Any],
    published_at: datetime,
    expires_at: datetime,
) -> None:
    container = _get_vaults_container()
    user_id = vault_document.get("user_id")
    vault_id = vault_document.get("id")

    if not user_id:
        raise ValueError(f"Vault missing user_id partition key. vault_id={vault_id}")

    patched_document = dict(vault_document)
    patched_document["status"] = "grace_period"
    patched_document["grace_period_expires_at"] = expires_at.isoformat()
    patched_document["grace_period_event_published_at"] = published_at.isoformat()

    try:
        container.replace_item(
            item=patched_document["id"],
            body=patched_document,
            partition_key=user_id,
        )
    except exceptions.CosmosHttpResponseError:
        logger.exception(
            "Failed to persist grace-period event marker. vault_id=%s",
            vault_id,
        )
        raise


def _publish_expiration_event(
    vault_document: Dict[str, Any],
    last_activity_at: datetime,
    expires_at: datetime,
    detected_at: datetime,
) -> None:
    event_grid_client = _get_event_grid_client()
    vault_id = str(vault_document.get("id"))
    user_id = vault_document.get("user_id")
    grace_period_days = int(vault_document.get("grace_period_days", 0))

    event = EventGridEvent(
        subject=f"/vaults/{vault_id}",
        event_type="GracePeriodExpired",
        data_version="1.0",
        data={
            "vault_id": vault_id,
            "user_id": user_id,
            "grace_period_days": grace_period_days,
            "last_activity_at": last_activity_at.isoformat(),
            "grace_period_expires_at": expires_at.isoformat(),
            "detected_at": detected_at.isoformat(),
        },
    )

    try:
        event_grid_client.send([event])
        logger.info("Published GracePeriodExpired event. vault_id=%s", vault_id)
    except Exception:
        logger.exception("Failed publishing Event Grid event. vault_id=%s", vault_id)
        raise


def main(mytimer: func.TimerRequest) -> None:
    now_utc = datetime.now(timezone.utc)
    logger.info("check_grace_periods timer fired at %s", now_utc.isoformat())

    if mytimer.past_due:
        logger.warning("check_grace_periods timer is running later than scheduled.")

    try:
        candidate_vaults = _query_candidate_vaults()
    except Exception:
        logger.exception("Timer job failed while querying candidate vaults.")
        return

    published_count = 0
    skipped_count = 0
    failed_count = 0

    for vault_document in candidate_vaults:
        vault_id = str(vault_document.get("id", "unknown"))

        try:
            last_activity_at = _get_last_activity_at(vault_document)
            if last_activity_at is None:
                skipped_count += 1
                logger.warning(
                    "Skipping vault due to missing/invalid activity timestamp. vault_id=%s",
                    vault_id,
                )
                continue

            grace_period_days = int(vault_document.get("grace_period_days", 0))
            expires_at = last_activity_at + timedelta(days=grace_period_days)
            if expires_at >= now_utc:
                skipped_count += 1
                logger.debug(
                    "Vault grace period not yet expired. vault_id=%s expires_at=%s",
                    vault_id,
                    expires_at.isoformat(),
                )
                continue

            _publish_expiration_event(
                vault_document=vault_document,
                last_activity_at=last_activity_at,
                expires_at=expires_at,
                detected_at=now_utc,
            )
            _mark_event_published(
                vault_document=vault_document,
                published_at=now_utc,
                expires_at=expires_at,
            )

            published_count += 1
        except Exception:
            failed_count += 1
            logger.exception("Failed processing grace check for vault_id=%s", vault_id)

    logger.info(
        "Grace-period check finished. published=%s skipped=%s failed=%s",
        published_count,
        skipped_count,
        failed_count,
    )
