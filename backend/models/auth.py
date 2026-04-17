from __future__ import annotations

from pydantic import BaseModel, Field


class AuthRegisterRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
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
    email_verification_required: bool
    verification_url: str
    verification_token: str | None = None


class AuthMeResponse(BaseModel):
    user_id: str
    email: str
    email_verified: bool
