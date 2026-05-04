from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib import request as urllib_request

import azure.functions as func
from azure.cosmos import CosmosClient, exceptions
from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

try:
    from azure.monitor.opentelemetry import configure_azure_monitor
except Exception:  # pragma: no cover - optional dependency resolution
    configure_azure_monitor = None

_monitoring_configured = False


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

_cosmos_client: Optional[CosmosClient] = None
_vaults_container = None
_credential: Optional[DefaultAzureCredential] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    if not connection_string:
        raise RuntimeError("Environment variable COSMOS_CONNECTION_STRING is required.")

    database_name = os.getenv("COSMOS_DATABASE_NAME", "last-writes-db")
    container_name = os.getenv("COSMOS_VAULTS_CONTAINER", "vaults")

    _cosmos_client = CosmosClient.from_connection_string(connection_string)
    database_client = _cosmos_client.get_database_client(database_name)
    _vaults_container = database_client.get_container_client(container_name)
    return _vaults_container


def _get_credential() -> DefaultAzureCredential:
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential


def _get_vault(vault_id: str) -> Optional[Dict[str, Any]]:
    container = _get_vaults_container()
    query = "SELECT * FROM c WHERE c.id = @vault_id AND c.doc_type = 'vault'"
    parameters = [{"name": "@vault_id", "value": vault_id}]
    items = list(
        container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True,
        )
    )
    return items[0] if items else None


def _update_vault(vault_document: Dict[str, Any], update_data: Dict[str, Any]) -> Dict[str, Any]:
    container = _get_vaults_container()
    patched_document = dict(vault_document)
    patched_document.update(update_data)
    return container.replace_item(item=vault_document, body=patched_document)


def _append_or_replace_env(container_definition: Dict[str, Any], name: str, value: str) -> None:
    env_items = container_definition.get("env")
    if not isinstance(env_items, list):
        env_items = []

    for env_item in env_items:
        if isinstance(env_item, dict) and env_item.get("name") == name:
            env_item["value"] = value
            container_definition["env"] = env_items
            return

    env_items.append({"name": name, "value": value})
    container_definition["env"] = env_items


def _management_request(method: str, url: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    credential = _get_credential()
    token = credential.get_token("https://management.azure.com/.default").token

    body = None
    headers = {"Authorization": f"Bearer {token}"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib_request.Request(url=url, data=body, headers=headers, method=method)
    with urllib_request.urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8").strip()
        if not raw:
            return {}
        return json.loads(raw)


def _start_delivery_job(vault_id: str, event_id: str) -> Optional[str]:
    subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID", "").strip()
    resource_group = os.getenv("CONTAINER_APPS_RESOURCE_GROUP", "").strip()
    job_name = os.getenv("CONTAINER_APPS_JOB_NAME", "").strip()
    api_version = os.getenv("CONTAINER_APPS_API_VERSION", "2024-03-01").strip()

    if not subscription_id or not resource_group or not job_name:
        raise RuntimeError(
            "AZURE_SUBSCRIPTION_ID, CONTAINER_APPS_RESOURCE_GROUP and CONTAINER_APPS_JOB_NAME are required."
        )

    job_base_url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}/providers/Microsoft.App/jobs/{job_name}"
    )

    job_document = _management_request("GET", f"{job_base_url}?api-version={api_version}")
    template = (
        job_document.get("properties", {}).get("template", {})
        if isinstance(job_document, dict)
        else {}
    )
    containers = template.get("containers", [])
    if not isinstance(containers, list) or not containers:
        raise RuntimeError("Container Apps Job template does not contain any containers.")

    primary_container = dict(containers[0])
    _append_or_replace_env(primary_container, "VAULT_ID", vault_id)
    _append_or_replace_env(primary_container, "DELIVERY_EVENT_ID", event_id)
    containers[0] = primary_container

    start_payload: Dict[str, Any] = {"containers": containers}
    init_containers = template.get("initContainers")
    if isinstance(init_containers, list) and init_containers:
        start_payload["initContainers"] = init_containers

    start_response = _management_request(
        "POST",
        f"{job_base_url}/start?api-version={api_version}",
        payload=start_payload,
    )
    return str(start_response.get("name", "")).strip() or None


def main(event: func.EventGridEvent) -> None:
    _configure_monitoring()
    logger.info(
        "GracePeriodExpired event received. id=%s type=%s subject=%s",
        event.id,
        event.event_type,
        event.subject,
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
        logger.error("vault_id was not found in event payload or subject. event_id=%s", event.id)
        return

    try:
        vault_document = _get_vault(vault_id)
    except exceptions.CosmosHttpResponseError:
        logger.exception("Cosmos query failed while loading vault_id=%s", vault_id)
        return

    if vault_document is None:
        logger.warning("Vault not found for job start. vault_id=%s", vault_id)
        return

    current_status = str(vault_document.get("status", "active")).strip().lower()
    if current_status == "delivered":
        logger.info("Vault already delivered. event_id=%s vault_id=%s", event.id, vault_id)
        return

    if current_status == "delivery_initiated" and str(
        vault_document.get("delivery_job_started_at", "")
    ).strip():
        logger.info(
            "Delivery job already started for vault. event_id=%s vault_id=%s",
            event.id,
            vault_id,
        )
        return

    try:
        claimed_vault = _update_vault(
            vault_document,
            {
                "status": "delivery_initiated",
                "delivery_initiated_at": _now_iso(),
                "delivery_error": None,
                "delivery_trigger": {
                    "event_id": event.id,
                    "event_type": event.event_type,
                    "processed_at": _now_iso(),
                },
            },
        )
    except exceptions.CosmosHttpResponseError:
        logger.exception("Failed to claim vault for delivery. vault_id=%s", vault_id)
        return

    try:
        execution_name = _start_delivery_job(vault_id=vault_id, event_id=event.id)
        _update_vault(
            claimed_vault,
            {
                "delivery_job_started_at": _now_iso(),
                "delivery_job_execution_name": execution_name,
            },
        )
        logger.info(
            "Delivery job started successfully. vault_id=%s execution=%s",
            vault_id,
            execution_name,
        )
    except Exception as exc:
        logger.exception("Failed to start delivery job. vault_id=%s", vault_id)
        try:
            _update_vault(
                claimed_vault,
                {
                    "delivery_error": str(exc),
                },
            )
        except Exception:
            logger.exception("Failed to persist delivery start error. vault_id=%s", vault_id)
