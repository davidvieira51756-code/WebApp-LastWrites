from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


def _env_to_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


class DeliveryEmailService:
    def send_delivery_notification(
        self,
        *,
        recipients: Iterable[str],
        vault_name: str,
        vault_id: str,
        download_url: str,
        expires_at: str,
        owner_message: Optional[str],
    ) -> None:
        raise NotImplementedError


class LocalDeliveryEmailService(DeliveryEmailService):
    def send_delivery_notification(
        self,
        *,
        recipients: Iterable[str],
        vault_name: str,
        vault_id: str,
        download_url: str,
        expires_at: str,
        owner_message: Optional[str],
    ) -> None:
        logger.info(
            "LOCAL email dispatch. recipients=%s vault_id=%s vault_name=%s download_url=%s expires_at=%s owner_message=%s",
            list(recipients),
            vault_id,
            vault_name,
            download_url,
            expires_at,
            bool(owner_message),
        )


class AzureCommunicationEmailService(DeliveryEmailService):
    def __init__(self, connection_string: str, sender_address: str) -> None:
        from azure.communication.email import EmailClient

        self._client = EmailClient.from_connection_string(connection_string)
        self._sender_address = sender_address

    @staticmethod
    def _build_subject(vault_name: str) -> str:
        return f"[Last Writes] Delivery available for '{vault_name}'"

    @staticmethod
    def _build_plain_text(
        *,
        vault_name: str,
        download_url: str,
        expires_at: str,
        owner_message: Optional[str],
    ) -> str:
        lines = [
            f"A delivery package for vault '{vault_name}' is now available.",
            f"Download URL: {download_url}",
            f"Link expires at: {expires_at}",
        ]
        if owner_message:
            lines.extend(["", "Owner message:", owner_message.strip()])
        return "\n".join(lines)

    @staticmethod
    def _build_html(
        *,
        vault_name: str,
        download_url: str,
        expires_at: str,
        owner_message: Optional[str],
    ) -> str:
        owner_section = ""
        if owner_message and owner_message.strip():
            owner_section = (
                "<p><strong>Owner message</strong></p>"
                f"<p>{owner_message.strip()}</p>"
            )
        return (
            f"<html><body><p>A delivery package for vault '<strong>{vault_name}</strong>' is now available.</p>"
            f"<p><a href=\"{download_url}\">Download delivery package</a></p>"
            f"<p>This link expires at {expires_at}.</p>"
            f"{owner_section}</body></html>"
        )

    def send_delivery_notification(
        self,
        *,
        recipients: Iterable[str],
        vault_name: str,
        vault_id: str,
        download_url: str,
        expires_at: str,
        owner_message: Optional[str],
    ) -> None:
        for recipient in recipients:
            message = {
                "senderAddress": self._sender_address,
                "recipients": {"to": [{"address": recipient}]},
                "content": {
                    "subject": self._build_subject(vault_name),
                    "plainText": self._build_plain_text(
                        vault_name=vault_name,
                        download_url=download_url,
                        expires_at=expires_at,
                        owner_message=owner_message,
                    ),
                    "html": self._build_html(
                        vault_name=vault_name,
                        download_url=download_url,
                        expires_at=expires_at,
                        owner_message=owner_message,
                    ),
                },
            }
            poller = self._client.begin_send(message)
            result = poller.result()
            logger.info(
                "ACS delivery email sent. vault_id=%s recipient=%s result=%s",
                vault_id,
                recipient,
                getattr(result, "id", None) or getattr(result, "message_id", None) or "completed",
            )


def build_delivery_email_service() -> DeliveryEmailService:
    local_dev_mode = _env_to_bool(os.getenv("LOCAL_DEV_MODE", "false"))
    connection_string = os.getenv("ACS_EMAIL_CONNECTION_STRING", "").strip()
    sender_address = os.getenv("ACS_EMAIL_SENDER", "").strip()

    if local_dev_mode:
        return LocalDeliveryEmailService()

    if not connection_string or not sender_address:
        raise ValueError(
            "ACS_EMAIL_CONNECTION_STRING and ACS_EMAIL_SENDER are required outside LOCAL_DEV_MODE."
        )

    return AzureCommunicationEmailService(
        connection_string=connection_string,
        sender_address=sender_address,
    )
