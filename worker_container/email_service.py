from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from azure.communication.email import EmailClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailSendResult:
    status: str
    recipient: str
    subject: str
    message_id: Optional[str] = None
    error: Optional[str] = None

    @property
    def sent(self) -> bool:
        return self.status == "sent"

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    @property
    def skipped(self) -> bool:
        return self.status == "skipped"


class EmailService:
    def __init__(
        self,
        *,
        connection_string: Optional[str] = None,
        sender_address: Optional[str] = None,
    ) -> None:
        self._connection_string = (
            connection_string or os.getenv("ACS_EMAIL_CONNECTION_STRING", "")
        ).strip()
        self._sender_address = (
            sender_address or os.getenv("ACS_EMAIL_SENDER", "")
        ).strip()
        self._client: Optional[EmailClient] = None

    def is_configured(self) -> bool:
        return bool(self._connection_string and self._sender_address)

    def _get_client(self) -> EmailClient:
        if self._client is None:
            self._client = EmailClient.from_connection_string(self._connection_string)
        return self._client

    def send_email(
        self,
        *,
        recipient: str,
        subject: str,
        plain_text: str,
        html: str,
    ) -> EmailSendResult:
        normalized_recipient = recipient.strip().lower()
        if not self.is_configured():
            logger.warning(
                "ACS email configuration is missing; email send skipped. recipient=%s subject=%s",
                normalized_recipient,
                subject,
            )
            return EmailSendResult(
                status="skipped",
                recipient=normalized_recipient,
                subject=subject,
            )

        message = {
            "senderAddress": self._sender_address,
            "recipients": {"to": [{"address": normalized_recipient}]},
            "content": {
                "subject": subject,
                "plainText": plain_text,
                "html": html,
            },
        }

        try:
            poller = self._get_client().begin_send(message)
            result = poller.result()
            message_id = getattr(result, "id", None)
            logger.info(
                "ACS email sent successfully. recipient=%s subject=%s message_id=%s",
                normalized_recipient,
                subject,
                message_id,
            )
            return EmailSendResult(
                status="sent",
                recipient=normalized_recipient,
                subject=subject,
                message_id=message_id,
            )
        except Exception as exc:
            logger.warning(
                "ACS email send failed. recipient=%s subject=%s error=%s",
                normalized_recipient,
                subject,
                exc,
            )
            return EmailSendResult(
                status="failed",
                recipient=normalized_recipient,
                subject=subject,
                error=str(exc),
            )
