from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, secret_key: Optional[str] = None) -> None:
        default_secret = "local-dev-change-me-auth-secret"
        self._secret_key = (secret_key or os.getenv("AUTH_SECRET_KEY", default_secret)).strip()
        if not self._secret_key:
            raise ValueError("Environment variable AUTH_SECRET_KEY is required.")

        local_dev_mode = self._env_to_bool(os.getenv("LOCAL_DEV_MODE", ""))
        if self._secret_key == default_secret and not local_dev_mode:
            logger.warning(
                "AUTH_SECRET_KEY is using the local default value outside LOCAL_DEV_MODE. "
                "Set AUTH_SECRET_KEY to a strong secret in non-local environments."
            )

        self._secret_key_bytes = self._secret_key.encode("utf-8")
        self._access_token_ttl_minutes = self._get_env_int("AUTH_ACCESS_TOKEN_TTL_MINUTES", 120)
        self._verification_ttl_minutes = self._get_env_int(
            "EMAIL_VERIFICATION_TOKEN_TTL_MINUTES", 24 * 60
        )
        self._password_iterations = self._get_env_int("AUTH_PASSWORD_PBKDF2_ITERATIONS", 260000)
        self._require_email_verification = self._env_to_bool(
            os.getenv("AUTH_REQUIRE_EMAIL_VERIFICATION", "true")
        )
        self._frontend_verify_url = (
            os.getenv("FRONTEND_VERIFY_EMAIL_URL", "http://localhost:3000/verify-email")
            .strip()
            .rstrip("/")
        )

    @staticmethod
    def _env_to_bool(value: str) -> bool:
        return value.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _get_env_int(name: str, default: int) -> int:
        raw = os.getenv(name, str(default)).strip()
        try:
            parsed = int(raw)
        except ValueError:
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _b64url_encode(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    @staticmethod
    def _b64url_decode(raw: str) -> bytes:
        padding = "=" * (-len(raw) % 4)
        return base64.urlsafe_b64decode((raw + padding).encode("ascii"))

    @staticmethod
    def normalize_email(email: str) -> str:
        return email.strip().lower()

    @staticmethod
    def hash_verification_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def validate_password(password: str) -> None:
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters long.")

    def hash_password(self, password: str) -> str:
        self.validate_password(password)
        salt = secrets.token_hex(16)
        derived_key = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            self._password_iterations,
        )
        encoded_key = self._b64url_encode(derived_key)
        return f"pbkdf2_sha256${self._password_iterations}${salt}${encoded_key}"

    def verify_password(self, password: str, stored_hash: str) -> bool:
        try:
            scheme, iterations_raw, salt, encoded_key = stored_hash.split("$", 3)
            if scheme != "pbkdf2_sha256":
                return False
            iterations = int(iterations_raw)
        except ValueError:
            return False

        candidate_key = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        )
        candidate_encoded = self._b64url_encode(candidate_key)
        return hmac.compare_digest(candidate_encoded, encoded_key)

    def _encode_jwt(self, payload: Dict[str, Any]) -> str:
        header = {"alg": "HS256", "typ": "JWT"}
        encoded_header = self._b64url_encode(
            json.dumps(header, separators=(",", ":")).encode("utf-8")
        )
        encoded_payload = self._b64url_encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )

        signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
        signature = hmac.new(
            self._secret_key_bytes,
            signing_input,
            digestmod=hashlib.sha256,
        ).digest()
        encoded_signature = self._b64url_encode(signature)
        return f"{encoded_header}.{encoded_payload}.{encoded_signature}"

    def verify_access_token(self, token: str) -> Dict[str, Any]:
        if not token or token.count(".") != 2:
            raise ValueError("Invalid token format.")

        encoded_header, encoded_payload, encoded_signature = token.split(".", 2)
        signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
        expected_signature = hmac.new(
            self._secret_key_bytes,
            signing_input,
            digestmod=hashlib.sha256,
        ).digest()
        provided_signature = self._b64url_decode(encoded_signature)

        if not hmac.compare_digest(provided_signature, expected_signature):
            raise ValueError("Invalid token signature.")

        try:
            payload = json.loads(self._b64url_decode(encoded_payload).decode("utf-8"))
        except Exception as exc:
            raise ValueError("Invalid token payload.") from exc

        exp_timestamp = payload.get("exp")
        if not isinstance(exp_timestamp, int):
            raise ValueError("Token is missing expiration.")

        now_timestamp = int(datetime.now(timezone.utc).timestamp())
        if exp_timestamp <= now_timestamp:
            raise ValueError("Token has expired.")

        subject = str(payload.get("sub", "")).strip()
        if not subject:
            raise ValueError("Token subject is missing.")

        return payload

    def issue_access_token(self, user: Dict[str, Any]) -> Dict[str, str]:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=self._access_token_ttl_minutes)
        payload = {
            "sub": str(user.get("id", "")),
            "email": str(user.get("email", "")),
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        }
        token = self._encode_jwt(payload)

        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_at": expires_at.isoformat(),
        }

    def issue_email_verification(self) -> Dict[str, str]:
        token = secrets.token_urlsafe(32)
        token_hash = self.hash_verification_token(token)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=self._verification_ttl_minutes)
        return {
            "token": token,
            "token_hash": token_hash,
            "expires_at": expires_at.isoformat(),
        }

    def build_email_verification_url(self, token: str) -> str:
        return f"{self._frontend_verify_url}?token={quote(token)}"

    def should_require_email_verification(self) -> bool:
        return self._require_email_verification
