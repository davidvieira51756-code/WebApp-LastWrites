from __future__ import annotations

import logging
import os
from threading import Lock

_configured = False
_lock = Lock()


def configure_monitoring() -> None:
    global _configured

    if _configured:
        return

    connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "").strip()
    if not connection_string:
        return

    with _lock:
        if _configured:
            return
        try:
            from azure.monitor.opentelemetry import configure_azure_monitor

            configure_azure_monitor(connection_string=connection_string)
            _configured = True
        except Exception:
            logging.getLogger(__name__).exception(
                "Failed to configure Application Insights telemetry."
            )
