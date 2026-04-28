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
    DISABLED = "disabled"


class ActivationRequest(BaseModel):
    recipient_email: str = Field(..., min_length=3, max_length=320)
    requested_at: str = Field(..., min_length=1)
    reason: Optional[str] = Field(default=None, max_length=1000)


class VaultFileMetadata(BaseModel):
    id: str = Field(..., min_length=1)
    file_name: str = Field(..., min_length=1, max_length=512)
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
    grace_period_days: int = Field(..., ge=1, le=3650)
    status: VaultStatus = VaultStatus.ACTIVE
    recipients: List[str] = Field(default_factory=list)
    activation_threshold: int = Field(default=1, ge=1, le=100)


class VaultCreate(VaultBase):
    user_id: Optional[str] = Field(default=None, min_length=1, max_length=128)


class VaultUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    owner_message: Optional[str] = Field(default=None, max_length=4000)
    grace_period_days: Optional[int] = Field(default=None, ge=1, le=3650)
    status: Optional[VaultStatus] = None
    recipients: Optional[List[str]] = None
    activation_threshold: Optional[int] = Field(default=None, ge=1, le=100)


class Vault(VaultBase):
    id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1, max_length=128)
    key_kid: Optional[str] = None
    key_version: Optional[str] = None
    public_jwk: Optional[Dict[str, str]] = None
    activation_requests: List[ActivationRequest] = Field(default_factory=list)
    files: List[VaultFileMetadata] = Field(default_factory=list)
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
    status: VaultStatus
    grace_period_days: int
    activation_threshold: int
    activation_requests_count: int
    has_requested_activation: bool
    grace_period_expires_at: Optional[str] = None
    delivered_at: Optional[str] = None
    delivery_available: bool = False
