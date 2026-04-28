from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_configured_services: set[str] = set()


def _app_insights_connection_string() -> str:
    return (
        os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "").strip()
        or os.getenv("APPINSIGHTS_CONNECTIONSTRING", "").strip()
    )


def configure_application_insights(service_name: str) -> bool:
    connection_string = _app_insights_connection_string()
    if not connection_string or service_name in _configured_services:
        return False

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
    except Exception:
        logger.warning(
            "Application Insights SDK is unavailable; telemetry was not configured for service=%s",
            service_name,
        )
        return False

    os.environ.setdefault("OTEL_SERVICE_NAME", service_name)
    configure_azure_monitor(connection_string=connection_string)
    _configured_services.add(service_name)
    return True
