from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional
from uuid import uuid4

from azure.cosmos import CosmosClient, PartitionKey, exceptions

logger = logging.getLogger(__name__)


class CosmosService:
    def __init__(self, connection_string: Optional[str] = None) -> None:
        self._connection_string = connection_string or os.getenv("COSMOS_CONNECTION_STRING")
        if not self._connection_string:
            raise ValueError(
                "Environment variable COSMOS_CONNECTION_STRING is required."
            )

        self._database_name = os.getenv("COSMOS_DATABASE_NAME", "last-writes-db")
        self._container_name = os.getenv("COSMOS_VAULTS_CONTAINER", "vaults")
        throughput_value = os.getenv("COSMOS_CONTAINER_THROUGHPUT", "400")
        self._container_throughput = int(throughput_value)

        self._client: Optional[CosmosClient] = None
        self._database = None
        self._container = None

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
            logger.info(
                "Cosmos DB initialized. database=%s container=%s",
                self._database_name,
                self._container_name,
            )
        except Exception:
            logger.exception("Failed to initialize Cosmos DB resources.")
            raise

    def _get_container(self):
        if self._container is None:
            raise RuntimeError("CosmosService is not initialized.")
        return self._container

    def create_vault(self, vault_data: Dict[str, Any]) -> Dict[str, Any]:
        container = self._get_container()
        payload = dict(vault_data)
        payload["id"] = payload.get("id") or str(uuid4())

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
        query = "SELECT * FROM c WHERE c.id = @id"
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

    def list_vaults(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        container = self._get_container()
        query = "SELECT * FROM c"
        parameters = None

        if user_id:
            query = "SELECT * FROM c WHERE c.user_id = @user_id"
            parameters = [{"name": "@user_id", "value": user_id}]

        try:
            items = list(
                container.query_items(
                    query=query,
                    parameters=parameters,
                    enable_cross_partition_query=True,
                )
            )
            return items
        except exceptions.CosmosHttpResponseError:
            logger.exception("Cosmos DB list query failed.")
            raise

    def add_recipient_to_vault(
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

        recipients = existing_item.get("recipients", [])
        if not isinstance(recipients, list):
            recipients = []

        normalized_email = email.strip().lower()
        email_exists = any(
            isinstance(recipient, str)
            and recipient.strip().lower() == normalized_email
            for recipient in recipients
        )

        if not email_exists:
            recipients.append(email.strip())
            logger.info("Added recipient to vault. vault_id=%s email=%s", vault_id, email)
        else:
            logger.info(
                "Recipient already exists in vault. vault_id=%s email=%s",
                vault_id,
                email,
            )

        updated_item = dict(existing_item)
        updated_item["recipients"] = recipients

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

        recipients = existing_item.get("recipients", [])
        if not isinstance(recipients, list):
            recipients = []

        normalized_email = email.strip().lower()
        updated_recipients = [
            recipient
            for recipient in recipients
            if not (
                isinstance(recipient, str)
                and recipient.strip().lower() == normalized_email
            )
        ]

        updated_item = dict(existing_item)
        updated_item["recipients"] = updated_recipients

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
