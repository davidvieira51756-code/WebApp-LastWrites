from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AuthRegisterRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    username: str = Field(..., min_length=3, max_length=32)
    full_name: str = Field(..., min_length=1, max_length=120)
    birth_date: str = Field(..., min_length=10, max_length=10)
    password: str = Field(..., min_length=8, max_length=256)


class AuthLoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    password: str = Field(..., min_length=1, max_length=256)


class EmailVerificationRequest(BaseModel):
    token: str = Field(..., min_length=10, max_length=2048)


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str
    user_id: str
    email: str
    email_verified: bool


class AuthRegisterResponse(BaseModel):
    message: str
    user_id: str
    email: str
    username: str
    email_verification_required: bool
    verification_url: str
    verification_token: str | None = None


class AuthMeResponse(BaseModel):
    user_id: str
    email: str
    email_verified: bool
    username: str
    full_name: str
    birth_date: str
    display_name_preference: Literal["username", "real_name"]
    account_status: Literal["active", "pending_deletion"]
    last_activity_at: str | None = None
    account_deletion_started_at: str | None = None
    account_deletion_scheduled_at: str | None = None


class AuthProfileUpdateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    full_name: str = Field(..., min_length=1, max_length=120)
    birth_date: str = Field(..., min_length=10, max_length=10)
    display_name_preference: Literal["username", "real_name"]


class AuthChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=8, max_length=256)


class AuthDeleteAccountRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=256)
