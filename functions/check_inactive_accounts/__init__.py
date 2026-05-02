from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import azure.functions as func
from azure.cosmos import CosmosClient, exceptions

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

try:
    from azure.monitor.opentelemetry import configure_azure_monitor
except Exception:  # pragma: no cover - optional dependency resolution
    configure_azure_monitor = None

_monitoring_configured = False
_cosmos_client: Optional[CosmosClient] = None
_container = None
BLOCKING_VAULT_STATUSES = {"active", "pending_activation", "grace_period", "delivery_initiated"}


def _configure_monitoring() -> None:
    global _monitoring_configured

    if _monitoring_configured:
        return

    connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "").strip()
    if not connection_string or configure_azure_monitor is None:
        return

    try:
        configure_azure_monitor(connection_string=connection_string)
        _monitoring_configured = True
    except Exception:
        logger.exception("Failed to configure Application Insights telemetry.")


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


def _get_container():
    global _cosmos_client
    global _container

    if _container is not None:
        return _container

    connection_string = os.getenv("COSMOS_CONNECTION_STRING")
    if not connection_string:
        raise RuntimeError("Environment variable COSMOS_CONNECTION_STRING is required.")

    database_name = os.getenv("COSMOS_DATABASE_NAME", "last-writes-db")
    container_name = os.getenv("COSMOS_VAULTS_CONTAINER", "vaults")
    _cosmos_client = CosmosClient.from_connection_string(connection_string)
    database_client = _cosmos_client.get_database_client(database_name)
    _container = database_client.get_container_client(container_name)
    return _container


def _list_users() -> List[Dict[str, Any]]:
    container = _get_container()
    query = "SELECT * FROM c WHERE c.doc_type = 'user'"
    return list(container.query_items(query=query, enable_cross_partition_query=True))


def _list_vaults_for_user(user_id: str) -> List[Dict[str, Any]]:
    container = _get_container()
    query = "SELECT * FROM c WHERE c.doc_type = 'vault' AND c.user_id = @user_id"
    parameters = [{"name": "@user_id", "value": user_id}]
    return list(
        container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True,
        )
    )


def _update_user(user_document: Dict[str, Any], update_data: Dict[str, Any]) -> None:
    container = _get_container()
    patched_document = dict(user_document)
    patched_document.update(update_data)
    container.replace_item(item=user_document, body=patched_document)


def _has_blocking_vaults(user_id: str) -> bool:
    for vault_document in _list_vaults_for_user(user_id):
        status_value = str(vault_document.get("status", "active")).strip().lower()
        if status_value in BLOCKING_VAULT_STATUSES:
            return True
    return False


def main(mytimer: func.TimerRequest) -> None:
    _configure_monitoring()
    now_utc = datetime.now(timezone.utc)
    logger.info("check_inactive_accounts timer fired at %s", now_utc.isoformat())

    if mytimer.past_due:
        logger.warning("check_inactive_accounts timer is running later than scheduled.")

    inactivity_days = max(1, int(os.getenv("ACCOUNT_INACTIVITY_DAYS", "365")))
    deletion_grace_days = max(1, int(os.getenv("ACCOUNT_DELETION_GRACE_DAYS", "30")))
    inactivity_cutoff = now_utc - timedelta(days=inactivity_days)

    try:
        users = _list_users()
    except exceptions.CosmosHttpResponseError:
        logger.exception("Failed to list users for inactivity processing.")
        return

    marked_count = 0
    restored_count = 0
    skipped_count = 0

    for user_document in users:
        user_id = str(user_document.get("id", "")).strip()
        if not user_id:
            skipped_count += 1
            continue

        try:
            has_blocking_vaults = _has_blocking_vaults(user_id)
        except exceptions.CosmosHttpResponseError:
            logger.exception("Failed to inspect vaults for user_id=%s", user_id)
            skipped_count += 1
            continue

        account_status = str(user_document.get("account_status", "active")).strip().lower() or "active"
        last_activity_at = _parse_iso_datetime(user_document.get("last_activity_at"))
        is_inactive = last_activity_at is not None and last_activity_at <= inactivity_cutoff

        if has_blocking_vaults:
            if account_status == "pending_deletion":
                try:
                    _update_user(
                        user_document,
                        {
                            "account_status": "active",
                            "account_deletion_started_at": None,
                            "account_deletion_scheduled_at": None,
                        },
                    )
                    restored_count += 1
                except exceptions.CosmosHttpResponseError:
                    logger.exception("Failed to restore account status for user_id=%s", user_id)
                    skipped_count += 1
            else:
                skipped_count += 1
            continue

        if not is_inactive:
            skipped_count += 1
            continue

        if account_status == "pending_deletion":
            skipped_count += 1
            continue

        try:
            _update_user(
                user_document,
                {
                    "account_status": "pending_deletion",
                    "account_deletion_started_at": now_utc.isoformat(),
                    "account_deletion_scheduled_at": (now_utc + timedelta(days=deletion_grace_days)).isoformat(),
                },
            )
            marked_count += 1
        except exceptions.CosmosHttpResponseError:
            logger.exception("Failed to mark account for deletion. user_id=%s", user_id)
            skipped_count += 1

    logger.info(
        "Inactive account check finished. marked=%s restored=%s skipped=%s",
        marked_count,
        restored_count,
        skipped_count,
    )
