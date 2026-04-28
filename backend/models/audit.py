from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class AuditEventResponse(BaseModel):
    id: str = Field(..., min_length=1)
    owner_user_id: str = Field(..., min_length=1)
    vault_id: Optional[str] = None
    event_type: str = Field(..., min_length=1)
    occurred_at: str = Field(..., min_length=1)
    actor_user_id: Optional[str] = None
    actor_email: Optional[str] = None
    actor_type: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
