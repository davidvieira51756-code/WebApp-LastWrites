from __future__ import annotations

import logging
import os
from typing import Dict, Optional

from azure.keyvault.secrets import SecretClient

try:
    from backend.services.azure_identity_service import build_key_vault_credential
except ModuleNotFoundError:
    from services.azure_identity_service import build_key_vault_credential

logger = logging.getLogger(__name__)


class KeyVaultService:
    def __init__(self, key_vault_url: Optional[str] = None) -> None:
        self._key_vault_url = (key_vault_url or os.getenv("KEY_VAULT_URL", "")).strip()
        if self._looks_like_placeholder_url(self._key_vault_url):
            logger.info(
                "KEY_VAULT_URL appears to be a template placeholder; using local environment fallbacks."
            )
            self._key_vault_url = ""
        self._cosmos_secret_name = os.getenv(
            "COSMOS_CONNECTION_STRING_SECRET_NAME",
            "COSMOS-CONNECTION-STRING",
        )
        self._blob_secret_name = os.getenv(
            "BLOB_CONNECTION_STRING_SECRET_NAME",
            "BLOB-CONNECTION-STRING",
        )

        self._credential = None
        self._secret_client: Optional[SecretClient] = None

    @staticmethod
    def _looks_like_placeholder_url(url: str) -> bool:
        if not url:
            return False
        normalized = url.lower()
        return "your-key-vault-name" in normalized or "example" in normalized

    def _get_local_connection_strings(self) -> Dict[str, Optional[str]]:
        return {
            "cosmos_connection_string": os.getenv("COSMOS_CONNECTION_STRING"),
            "blob_connection_string": os.getenv("BLOB_CONNECTION_STRING"),
        }

    def _get_secret_client(self) -> Optional[SecretClient]:
        if not self._key_vault_url:
            return None

        if self._secret_client is None:
            self._credential = build_key_vault_credential()
            self._secret_client = SecretClient(
                vault_url=self._key_vault_url,
                credential=self._credential,
            )
            logger.info("Azure Key Vault client initialized for vault=%s", self._key_vault_url)

        return self._secret_client

    @staticmethod
    def _validate_connection_strings(
        cosmos_connection_string: Optional[str],
        blob_connection_string: Optional[str],
        source_label: str,
    ) -> Dict[str, str]:
        missing = []
        if not cosmos_connection_string:
            missing.append("COSMOS_CONNECTION_STRING")
        if not blob_connection_string:
            missing.append("BLOB_CONNECTION_STRING")

        if missing:
            raise ValueError(
                "Missing required connection strings from "
                f"{source_label}: {', '.join(missing)}"
            )

        return {
            "cosmos_connection_string": cosmos_connection_string,
            "blob_connection_string": blob_connection_string,
        }

    def get_connection_strings(self) -> Dict[str, str]:
        local_connection_strings = self._get_local_connection_strings()
        cosmos_connection_string = local_connection_strings["cosmos_connection_string"]
        blob_connection_string = local_connection_strings["blob_connection_string"]

        if not self._key_vault_url:
            logger.info(
                "KEY_VAULT_URL is not set; using local environment variables for connection strings."
            )
            return self._validate_connection_strings(
                cosmos_connection_string,
                blob_connection_string,
                "local environment variables",
            )

        try:
            secret_client = self._get_secret_client()
            if secret_client is None:
                raise RuntimeError("Secret client is not initialized.")

            cosmos_secret = secret_client.get_secret(self._cosmos_secret_name)
            blob_secret = secret_client.get_secret(self._blob_secret_name)

            cosmos_connection_string = cosmos_secret.value or cosmos_connection_string
            blob_connection_string = blob_secret.value or blob_connection_string

            logger.info("Connection strings fetched from Azure Key Vault.")
            return self._validate_connection_strings(
                cosmos_connection_string,
                blob_connection_string,
                "Azure Key Vault",
            )
        except Exception:
            logger.exception("Failed to fetch connection strings from Azure Key Vault.")
            raise
