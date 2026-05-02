from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class VaultStatus(str, Enum):
    ACTIVE = "active"
    PENDING_ACTIVATION = "pending_activation"
    GRACE_PERIOD = "grace_period"
    DELIVERY_INITIATED = "delivery_initiated"
    DELIVERED = "delivered"
    DELIVERED_ARCHIVED = "delivered_archived"
    DISABLED = "disabled"


class GracePeriodUnit(str, Enum):
    HOURS = "hours"
    DAYS = "days"


class ActivationRequest(BaseModel):
    recipient_email: str = Field(..., min_length=3, max_length=320)
    requested_at: str = Field(..., min_length=1)
    reason: Optional[str] = Field(default=None, max_length=1000)


class VaultRecipient(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    can_activate: bool = True


class DeliveryPackageMetadata(BaseModel):
    recipient_email: str = Field(..., min_length=3, max_length=320)
    file_name: str = Field(..., min_length=1, max_length=512)
    blob_name: str = Field(..., min_length=1, max_length=1024)
    container_name: str = Field(..., min_length=1, max_length=128)
    size_bytes: Optional[int] = Field(default=None, ge=0)
    checksum_sha256: Optional[str] = Field(default=None, min_length=32, max_length=128)
    delivered_at: str = Field(..., min_length=1)


class VaultFileMetadata(BaseModel):
    id: str = Field(..., min_length=1)
    file_name: str = Field(..., min_length=1, max_length=512)
    recipient_emails: List[str] = Field(default_factory=list)
    blob_name: str = Field(..., min_length=1, max_length=1024)
    container_name: str = Field(..., min_length=1, max_length=128)
    blob_url: Optional[str] = None
    content_type: Optional[str] = Field(default=None, max_length=255)
    blob_content_type: Optional[str] = Field(default=None, max_length=255)
    size_bytes: Optional[int] = Field(default=None, ge=0)
    ciphertext_size_bytes: Optional[int] = Field(default=None, ge=0)
    uploaded_at: str = Field(..., min_length=1)
    encrypted: bool = False
    algorithm: Optional[str] = Field(default=None, max_length=128)
    wrapped_key: Optional[str] = None
    iv: Optional[str] = None
    tag: Optional[str] = None
    key_kid: Optional[str] = None
    key_version: Optional[str] = None
    plaintext_sha256: Optional[str] = Field(default=None, min_length=32, max_length=128)
    ciphertext_sha256: Optional[str] = Field(default=None, min_length=32, max_length=128)


class VaultBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    owner_message: Optional[str] = Field(default=None, max_length=4000)
    grace_period_value: int = Field(..., ge=1, le=3650)
    grace_period_unit: GracePeriodUnit = GracePeriodUnit.DAYS
    status: VaultStatus = VaultStatus.ACTIVE
    recipients: List[VaultRecipient] = Field(default_factory=list)
    activation_threshold: int = Field(default=1, ge=1, le=100)


class VaultCreate(VaultBase):
    user_id: Optional[str] = Field(default=None, min_length=1, max_length=128)


class VaultUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    owner_message: Optional[str] = Field(default=None, max_length=4000)
    grace_period_value: Optional[int] = Field(default=None, ge=1, le=3650)
    grace_period_unit: Optional[GracePeriodUnit] = None
    status: Optional[VaultStatus] = None
    recipients: Optional[List[VaultRecipient]] = None
    activation_threshold: Optional[int] = Field(default=None, ge=1, le=100)


class Vault(VaultBase):
    id: str = Field(..., min_length=1)
    short_id: Optional[str] = Field(default=None, min_length=8, max_length=8)
    user_id: str = Field(..., min_length=1, max_length=128)
    key_kid: Optional[str] = None
    key_version: Optional[str] = None
    public_jwk: Optional[Dict[str, str]] = None
    activation_requests: List[ActivationRequest] = Field(default_factory=list)
    files: List[VaultFileMetadata] = Field(default_factory=list)
    delivery_packages: List[DeliveryPackageMetadata] = Field(default_factory=list)
    grace_period_started_at: Optional[str] = None
    grace_period_expires_at: Optional[str] = None
    last_check_in_at: Optional[str] = None
    delivery_blob_name: Optional[str] = None
    delivery_container_name: Optional[str] = None
    delivery_file_name: Optional[str] = None
    delivery_size_bytes: Optional[int] = Field(default=None, ge=0)
    delivery_checksum_sha256: Optional[str] = Field(default=None, min_length=32, max_length=128)
    delivery_initiated_at: Optional[str] = None
    delivered_at: Optional[str] = None
    delivery_error: Optional[str] = None
    delivery_job_started_at: Optional[str] = None
    delivery_job_execution_name: Optional[str] = None


class VaultResponse(Vault):
    pass


class ActivationRequestCreate(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=1000)


class RecipientVaultSummary(BaseModel):
    """Lightweight vault view exposed to recipients."""

    id: str
    name: str
    owner_display_name: Optional[str] = Field(default=None, max_length=120)
    owner_username: Optional[str] = Field(default=None, max_length=32)
    status: VaultStatus
    grace_period_value: int
    grace_period_unit: GracePeriodUnit
    activation_threshold: int
    activation_requests_count: int
    has_requested_activation: bool
    can_activate: bool = True
    grace_period_expires_at: Optional[str] = None
    delivered_at: Optional[str] = None
    delivery_available: bool = False


class AuditLogEntry(BaseModel):
    id: str
    event_type: str = Field(..., min_length=1, max_length=128)
    event_at: str = Field(..., min_length=1)
    owner_user_id: str = Field(..., min_length=1, max_length=128)
    vault_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    actor_user_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    actor_email: Optional[str] = Field(default=None, max_length=320)
    source: str = Field(default="api", min_length=1, max_length=64)
    metadata: Dict[str, object] = Field(default_factory=dict)
