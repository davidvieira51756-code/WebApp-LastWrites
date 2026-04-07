from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class VaultStatus(str, Enum):
    ACTIVE = "active"
    GRACE_PERIOD = "grace_period"
    DELIVERED = "delivered"
    DISABLED = "disabled"


class VaultBase(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=200)
    grace_period_days: int = Field(..., ge=1, le=3650)
    status: VaultStatus = VaultStatus.ACTIVE
    recipients: List[str] = Field(default_factory=list)


class VaultCreate(VaultBase):
    pass


class VaultUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    grace_period_days: Optional[int] = Field(default=None, ge=1, le=3650)
    status: Optional[VaultStatus] = None
    recipients: Optional[List[str]] = None


class Vault(VaultBase):
    id: str = Field(..., min_length=1)


class VaultResponse(Vault):
    pass
