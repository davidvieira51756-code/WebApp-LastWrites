from __future__ import annotations

import logging
import os
from typing import Optional

from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

logger = logging.getLogger(__name__)


def _env_to_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _is_local_dev_mode() -> bool:
    return _env_to_bool(os.getenv("LOCAL_DEV_MODE", "false"))


def _managed_identity_client_id() -> Optional[str]:
    return (
        os.getenv("MANAGED_IDENTITY_CLIENT_ID", "").strip()
        or os.getenv("AZURE_CLIENT_ID", "").strip()
        or None
    )


def build_key_vault_credential():
    if _is_local_dev_mode():
        return DefaultAzureCredential(exclude_interactive_browser_credential=False)

    client_id = _managed_identity_client_id()
    credential = ManagedIdentityCredential(client_id=client_id)
    credential.get_token("https://vault.azure.net/.default")
    logger.info(
        "Managed Identity confirmed for Key Vault access. client_id=%s",
        client_id or "system-assigned",
    )
    return credential
