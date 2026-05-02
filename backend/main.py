from __future__ import annotations

import logging
import mimetypes
import os
import re
import secrets
import string
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from threading import Lock
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

try:
    from backend.models.auth import (
        AuthChangePasswordRequest,
        AuthDeleteAccountRequest,
        AuthLoginRequest,
        AuthMeResponse,
        AuthProfileUpdateRequest,
        AuthRegisterRequest,
        AuthRegisterResponse,
        AuthTokenResponse,
        EmailVerificationRequest,
    )
    from backend.models.vault import (
        ActivationRequestCreate,
        RecipientVaultSummary,
        AuditLogEntry,
        VaultCreate,
        VaultResponse,
        VaultUpdate,
    )
    from backend.services.auth_service import AuthService
    from backend.services.blob_service import BlobService
    from backend.services.cosmos_service import CosmosService
    from backend.services.email_service import EmailService
    from backend.services.file_crypto_service import decrypt_file_bytes, encrypt_file_bytes, sha256_hexdigest
    from backend.services.keyvault_service import KeyVaultService
    from backend.services.local_blob_service import LocalBlobService
    from backend.services.local_cosmos_service import LocalCosmosService
    from backend.services.monitoring import configure_monitoring
    from backend.services.vault_key_service import AzureVaultKeyService, LocalVaultKeyService
except ModuleNotFoundError:
    from models.auth import (
        AuthChangePasswordRequest,
        AuthDeleteAccountRequest,
        AuthLoginRequest,
        AuthMeResponse,
        AuthProfileUpdateRequest,
        AuthRegisterRequest,
        AuthRegisterResponse,
        AuthTokenResponse,
        EmailVerificationRequest,
    )
    from models.vault import (
        ActivationRequestCreate,
        RecipientVaultSummary,
        AuditLogEntry,
        VaultCreate,
        VaultResponse,
        VaultUpdate,
    )
    from services.auth_service import AuthService
    from services.blob_service import BlobService
    from services.cosmos_service import CosmosService
    from services.email_service import EmailService
    from services.file_crypto_service import decrypt_file_bytes, encrypt_file_bytes, sha256_hexdigest
    from services.keyvault_service import KeyVaultService
    from services.local_blob_service import LocalBlobService
    from services.local_cosmos_service import LocalCosmosService
    from services.monitoring import configure_monitoring
    from services.vault_key_service import AzureVaultKeyService, LocalVaultKeyService

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

EMAIL_ADDRESS_REGEX = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$"
)
USERNAME_REGEX = re.compile(r"^[A-Za-z0-9_]{3,32}$")
SHORT_ID_REGEX = re.compile(r"^[a-z0-9]{8}$")
auth_bearer = HTTPBearer(auto_error=False)
SAFE_FILENAME_REGEX = re.compile(r"[^A-Za-z0-9._() -]+")
SHORT_ID_ALPHABET = string.ascii_lowercase + string.digits
FINAL_IMMUTABLE_VAULT_STATUSES = {"delivered", "delivered_archived"}
FINAL_IMMUTABLE_VAULT_ERROR = "This vault has already been delivered and archived. It can no longer be modified."
DEFAULT_ALLOWED_UPLOAD_CONTENT_TYPES = {
    "application/json",
    "application/msword",
    "application/pdf",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/zip",
    "image/jpeg",
    "image/png",
    "text/csv",
    "text/markdown",
    "text/plain",
}
_login_rate_limit_lock = Lock()
_login_rate_limit_buckets: dict[str, deque[float]] = defaultdict(deque)


class RecipientCreateRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)


def normalize_recipients(recipients: List[str]) -> List[str]:
    normalized_recipients: List[str] = []
    seen_recipients = set()

    for recipient in recipients:
        if not isinstance(recipient, str):
            raise ValueError("Recipients must be email strings.")

        normalized_email = recipient.strip().lower()
        if not is_valid_email(normalized_email):
            raise ValueError(f"Invalid recipient email: {recipient}")
        if normalized_email in seen_recipients:
            continue

        seen_recipients.add(normalized_email)
        normalized_recipients.append(normalized_email)

    return normalized_recipients


def get_vault_file_metadata(vault_item: dict, file_id: str) -> Optional[dict]:
    files = vault_item.get("files", [])
    if not isinstance(files, list):
        return None

    return next(
        (
            file_item
            for file_item in files
            if isinstance(file_item, dict) and str(file_item.get("id")) == file_id
        ),
        None,
    )

app = FastAPI(title="Last Writes Backend API", version="1.0.0")

frontend_origins_env = os.getenv("FRONTEND_ORIGINS", "http://localhost:3000")
frontend_origins = [
    origin.strip() for origin in frontend_origins_env.split(",") if origin.strip()
]
if not frontend_origins:
    frontend_origins = ["http://localhost:3000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


def get_cosmos_service(request: Request) -> CosmosService:
    cosmos_service = getattr(request.app.state, "cosmos_service", None)
    if cosmos_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cosmos service is not initialized.",
        )
    return cosmos_service


def get_blob_service(request: Request) -> BlobService:
    blob_service = getattr(request.app.state, "blob_service", None)
    if blob_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Blob service is not initialized.",
        )
    return blob_service


def get_auth_service(request: Request) -> AuthService:
    auth_service = getattr(request.app.state, "auth_service", None)
    if auth_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service is not initialized.",
        )
    return auth_service


def get_email_service(request: Request) -> EmailService:
    email_service = getattr(request.app.state, "email_service", None)
    if email_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email service is not initialized.",
        )
    return email_service


def get_vault_key_service(request: Request):
    vault_key_service = getattr(request.app.state, "vault_key_service", None)
    if vault_key_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Vault key service is not initialized.",
        )
    return vault_key_service


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _should_expose_verification_token() -> bool:
    explicit_value = os.getenv("AUTH_EXPOSE_VERIFICATION_TOKEN", "true").strip()
    return _is_truthy(explicit_value)


def _parse_iso_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None

    normalized_value = value.strip()
    if normalized_value.endswith("Z"):
        normalized_value = f"{normalized_value[:-1]}+00:00"

    try:
        parsed_value = datetime.fromisoformat(normalized_value)
    except ValueError:
        return None

    if parsed_value.tzinfo is None:
        return parsed_value.replace(tzinfo=timezone.utc)
    return parsed_value.astimezone(timezone.utc)


def _parse_iso_date(value: str) -> Optional[date]:
    normalized_value = str(value or "").strip()
    if not normalized_value:
        return None

    try:
        return date.fromisoformat(normalized_value)
    except ValueError:
        return None


def validate_birth_date(value: str) -> str:
    parsed_birth_date = _parse_iso_date(value)
    if parsed_birth_date is None:
        raise ValueError("Birth date must use the YYYY-MM-DD format.")

    today = datetime.now(timezone.utc).date()
    age = today.year - parsed_birth_date.year - (
        (today.month, today.day) < (parsed_birth_date.month, parsed_birth_date.day)
    )
    if age < 13:
        raise ValueError("You must be at least 13 years old.")

    return parsed_birth_date.isoformat()


def normalize_username(value: str) -> str:
    normalized_value = str(value or "").strip().lower()
    if not USERNAME_REGEX.fullmatch(normalized_value):
        raise ValueError("Username must be 3-32 characters and use only letters, numbers, or underscores.")
    return normalized_value


def normalize_full_name(value: str) -> str:
    normalized_value = " ".join(str(value or "").strip().split())
    if not normalized_value:
        raise ValueError("Full name is required.")
    if len(normalized_value) > 120:
        raise ValueError("Full name must be 120 characters or fewer.")
    return normalized_value


def resolve_display_name_preference(value: str) -> str:
    normalized_value = str(value or "").strip().lower()
    if normalized_value not in {"username", "real_name"}:
        raise ValueError("Display name preference must be either 'username' or 'real_name'.")
    return normalized_value


def build_user_display_name(user_item: Optional[Dict[str, Any]]) -> Optional[str]:
    if not user_item:
        return None

    preference = str(user_item.get("display_name_preference", "username")).strip().lower()
    full_name = str(user_item.get("full_name", "")).strip()
    username = str(user_item.get("username", "")).strip()

    if preference == "real_name" and full_name:
        return full_name
    if username:
        return username
    if full_name:
        return full_name
    return None


def _generate_short_id() -> str:
    return "".join(secrets.choice(SHORT_ID_ALPHABET) for _ in range(8))


def generate_unique_short_id(cosmos_service: CosmosService) -> str:
    for _ in range(64):
        candidate = _generate_short_id()
        if cosmos_service.get_vault_by_short_id(candidate) is None:
            return candidate
    raise RuntimeError("Failed to generate a unique public vault identifier.")


def ensure_vault_short_ids(cosmos_service: CosmosService) -> None:
    seen_short_ids: set[str] = set()
    for vault_item in cosmos_service.list_vaults():
        internal_vault_id = str(vault_item.get("id", "")).strip()
        short_id = str(vault_item.get("short_id", "")).strip().lower()
        if SHORT_ID_REGEX.fullmatch(short_id) and short_id not in seen_short_ids:
            seen_short_ids.add(short_id)
            continue

        new_short_id = generate_unique_short_id(cosmos_service)
        cosmos_service.update_vault(internal_vault_id, {"short_id": new_short_id})
        seen_short_ids.add(new_short_id)


def resolve_vault_by_public_id(cosmos_service: CosmosService, public_vault_id: str) -> Optional[Dict[str, Any]]:
    normalized_id = str(public_vault_id or "").strip().lower()
    if not normalized_id:
        return None
    return cosmos_service.get_vault_by_short_id(normalized_id)


def build_public_vault_payload(vault_item: Dict[str, Any]) -> Dict[str, Any]:
    public_payload = dict(vault_item)
    public_payload["id"] = str(vault_item.get("short_id", "")).strip() or str(vault_item.get("id", "")).strip()
    if str(public_payload.get("delivery_blob_name", "")).strip():
        public_payload["delivery_file_name"] = "last-writes-delivery.zip"
    return public_payload


def is_vault_immutable(vault_item: Dict[str, Any]) -> bool:
    return str(vault_item.get("status", "")).strip().lower() in FINAL_IMMUTABLE_VAULT_STATUSES


def ensure_vault_is_mutable(vault_item: Dict[str, Any]) -> None:
    if is_vault_immutable(vault_item):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=FINAL_IMMUTABLE_VAULT_ERROR,
        )


def _build_attachment_headers(file_name: str) -> Dict[str, str]:
    normalized_name = sanitize_filename(file_name)
    fallback_name = normalized_name.encode("ascii", errors="ignore").decode("ascii") or "download.bin"
    fallback_name = fallback_name.replace('"', "")
    encoded_name = quote(normalized_name)
    return {
        "Content-Disposition": (
            f'attachment; filename="{fallback_name}"; filename*=UTF-8\'\'{encoded_name}'
        )
    }


def sanitize_filename(file_name: str) -> str:
    base_name = os.path.basename(str(file_name or "")).strip()
    normalized_name = base_name.replace("\x00", "")
    normalized_name = SAFE_FILENAME_REGEX.sub("-", normalized_name)
    normalized_name = re.sub(r"\s+", " ", normalized_name).strip(" .-_")

    if not normalized_name:
        normalized_name = f"upload-{uuid4().hex}.bin"

    stem, suffix = os.path.splitext(normalized_name)
    safe_stem = stem[:120] or f"upload-{uuid4().hex[:8]}"
    safe_suffix = suffix[:20]
    return f"{safe_stem}{safe_suffix}"[:160]


def _env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        return int(raw_value)
    except ValueError:
        return default


def _allowed_upload_content_types() -> set[str]:
    configured = os.getenv("UPLOAD_ALLOWED_CONTENT_TYPES", "").strip()
    if not configured:
        return set(DEFAULT_ALLOWED_UPLOAD_CONTENT_TYPES)

    values = {
        item.strip().lower()
        for item in configured.split(",")
        if item.strip()
    }
    return values or set(DEFAULT_ALLOWED_UPLOAD_CONTENT_TYPES)


def _resolve_upload_content_type(file: UploadFile, sanitized_file_name: str) -> str:
    provided_type = str(file.content_type or "").strip().lower()
    if provided_type:
        return provided_type

    guessed_type, _ = mimetypes.guess_type(sanitized_file_name)
    return (guessed_type or "application/octet-stream").lower()


def validate_upload(file: UploadFile, payload: bytes, sanitized_file_name: str) -> str:
    max_upload_bytes = max(1, _env_int("MAX_UPLOAD_BYTES", 10 * 1024 * 1024))
    payload_size = len(payload)
    if payload_size > max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the maximum allowed size of {max_upload_bytes} bytes.",
        )

    content_type = _resolve_upload_content_type(file, sanitized_file_name)
    if content_type not in _allowed_upload_content_types():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type: {content_type}.",
        )

    return content_type


def _login_rate_limit_key(request: Request, email: str) -> str:
    client_host = request.client.host if request.client else "unknown"
    return f"{client_host.lower()}|{email.lower()}"


def enforce_login_rate_limit(request: Request, email: str) -> None:
    max_attempts = max(1, _env_int("LOGIN_RATE_LIMIT_ATTEMPTS", 5))
    window_seconds = max(1, _env_int("LOGIN_RATE_LIMIT_WINDOW_SECONDS", 300))
    now_seconds = datetime.now(timezone.utc).timestamp()
    bucket_key = _login_rate_limit_key(request, email)

    with _login_rate_limit_lock:
        attempts = _login_rate_limit_buckets[bucket_key]
        while attempts and now_seconds - attempts[0] > window_seconds:
            attempts.popleft()

        if len(attempts) >= max_attempts:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts. Please try again later.",
            )

        attempts.append(now_seconds)


def write_audit_event(
    cosmos_service: CosmosService,
    *,
    event_type: str,
    owner_user_id: str,
    vault_id: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    actor_email: Optional[str] = None,
    source: str = "api",
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        cosmos_service.log_audit_event(
            event_type=event_type,
            owner_user_id=owner_user_id,
            vault_id=vault_id,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            source=source,
            metadata=metadata,
        )
    except Exception:
        logger.exception(
            "Failed to persist audit event. event_type=%s vault_id=%s owner_user_id=%s",
            event_type,
            vault_id,
            owner_user_id,
        )


def _is_vault_recipient(vault_item: Dict[str, Any], recipient_email: str) -> bool:
    recipients = vault_item.get("recipients", [])
    if not isinstance(recipients, list):
        return False

    normalized_email = recipient_email.strip().lower()
    return any(
        isinstance(candidate, str) and candidate.strip().lower() == normalized_email
        for candidate in recipients
    )


def ensure_vault_key_metadata(
    cosmos_service: CosmosService,
    vault_key_service,
    vault_item: Dict[str, Any],
) -> Dict[str, Any]:
    existing_key_kid = str(vault_item.get("key_kid", "")).strip()
    public_jwk = vault_item.get("public_jwk")
    if existing_key_kid and isinstance(public_jwk, dict) and public_jwk.get("n") and public_jwk.get("e"):
        return vault_item

    key_metadata = vault_key_service.ensure_vault_key(str(vault_item.get("id", "")))
    updated_vault = cosmos_service.update_vault(str(vault_item.get("id", "")), key_metadata)
    if updated_vault is None:
        merged_vault = dict(vault_item)
        merged_vault.update(key_metadata)
        return merged_vault
    return updated_vault


def _download_vault_file_bytes(
    blob_service: BlobService,
    vault_key_service,
    file_metadata: Dict[str, Any],
) -> bytes:
    container_name = str(file_metadata.get("container_name", "")).strip()
    blob_name = str(file_metadata.get("blob_name", "")).strip()
    if not container_name or not blob_name:
        raise ValueError("File metadata is missing blob location details.")

    blob_bytes = blob_service.download_blob_bytes(container_name=container_name, blob_name=blob_name)
    if not bool(file_metadata.get("encrypted", False)):
        return blob_bytes

    wrapped_key = str(file_metadata.get("wrapped_key", "")).strip()
    iv = str(file_metadata.get("iv", "")).strip()
    tag = str(file_metadata.get("tag", "")).strip()
    key_kid = str(file_metadata.get("key_kid", "")).strip()
    if not wrapped_key or not iv or not tag or not key_kid:
        raise ValueError("Encrypted file metadata is incomplete.")

    aes_key = vault_key_service.unwrap_file_key(key_kid=key_kid, wrapped_key=wrapped_key)
    plaintext = decrypt_file_bytes(blob_bytes, aes_key=aes_key, iv=iv, tag=tag)

    expected_checksum = str(file_metadata.get("plaintext_sha256", "")).strip()
    if expected_checksum and sha256_hexdigest(plaintext) != expected_checksum:
        raise ValueError("File integrity verification failed after decryption.")

    return plaintext


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(auth_bearer),
) -> Dict[str, Any]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    auth_service = get_auth_service(request)
    cosmos_service = get_cosmos_service(request)

    try:
        payload = auth_service.verify_access_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id = str(payload.get("sub", "")).strip()
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token subject.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user = cosmos_service.get_user_by_id(user_id)
    except Exception:
        logger.exception("Failed to resolve authenticated user id=%s", user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to resolve authenticated user.",
        ) from None

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account was not found.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def get_owned_vault_or_404(
    cosmos_service: CosmosService,
    public_vault_id: str,
    user_id: str,
) -> Dict[str, Any]:
    try:
        vault_item = resolve_vault_by_public_id(cosmos_service, public_vault_id)
    except Exception:
        logger.exception("Unhandled error while retrieving vault public_id=%s", public_vault_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve vault.",
        ) from None

    if vault_item is None or str(vault_item.get("user_id")) != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vault not found.",
        )

    return vault_item


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_ADDRESS_REGEX.fullmatch(email.strip()))


@app.on_event("startup")
def startup_event() -> None:
    try:
        configure_monitoring()
        local_dev_mode = os.getenv("LOCAL_DEV_MODE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

        if local_dev_mode:
            cosmos_service = LocalCosmosService()
            cosmos_service.initialize()
            blob_service = LocalBlobService()
            blob_service.initialize()
            auth_service = AuthService()
            email_service = EmailService()
            vault_key_service = LocalVaultKeyService()
            app.state.keyvault_service = None
            logger.info("Application startup completed in LOCAL_DEV_MODE.")
        else:
            keyvault_service = KeyVaultService()
            secure_settings = keyvault_service.get_connection_strings()

            cosmos_service = CosmosService(
                connection_string=secure_settings["cosmos_connection_string"]
            )
            cosmos_service.initialize()

            blob_service = BlobService(
                connection_string=secure_settings["blob_connection_string"]
            )
            auth_service = AuthService()
            email_service = EmailService()
            key_vault_url = os.getenv("KEY_VAULT_URL", "").strip()
            if key_vault_url:
                vault_key_service = AzureVaultKeyService(key_vault_url=key_vault_url)
            else:
                logger.warning(
                    "KEY_VAULT_URL is not configured. Falling back to LocalVaultKeyService. "
                    "This mode is for development only."
                )
                vault_key_service = LocalVaultKeyService()

            app.state.keyvault_service = keyvault_service
            logger.info("Application startup completed.")

        app.state.cosmos_service = cosmos_service
        app.state.blob_service = blob_service
        app.state.auth_service = auth_service
        app.state.email_service = email_service
        app.state.vault_key_service = vault_key_service
        ensure_vault_short_ids(cosmos_service)
    except Exception:
        logger.exception("Application startup failed during service initialization.")
        raise


@app.post(
    "/auth/register",
    response_model=AuthRegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_account(payload: AuthRegisterRequest, request: Request) -> AuthRegisterResponse:
    cosmos_service = get_cosmos_service(request)
    auth_service = get_auth_service(request)
    email_service = get_email_service(request)

    normalized_email = auth_service.normalize_email(payload.email)
    if not is_valid_email(normalized_email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid email is required.",
        )

    try:
        normalized_username = normalize_username(payload.username)
        normalized_full_name = normalize_full_name(payload.full_name)
        normalized_birth_date = validate_birth_date(payload.birth_date)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        existing_username_owner = cosmos_service.get_user_by_username(normalized_username)
    except Exception:
        logger.exception("Failed to validate username uniqueness. username=%s", normalized_username)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to register account.",
        ) from None

    if existing_username_owner is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this username already exists.",
        )

    try:
        password_hash = auth_service.hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    verification_payload = auth_service.issue_email_verification()
    user_document = {
        "email": normalized_email,
        "username": normalized_username,
        "full_name": normalized_full_name,
        "birth_date": normalized_birth_date,
        "display_name_preference": "username",
        "password_hash": password_hash,
        "is_email_verified": False,
        "verification_token_hash": verification_payload["token_hash"],
        "verification_token_expires_at": verification_payload["expires_at"],
    }

    try:
        created_user = cosmos_service.create_user(user_document)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception:
        logger.exception("Unhandled error while creating user account.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to register account.",
        ) from None

    verification_token = verification_payload["token"]
    verification_url = auth_service.build_email_verification_url(verification_token)
    verification_email_result = email_service.send_verification_email(
        recipient=normalized_email,
        verification_url=verification_url,
    )
    if verification_email_result.sent:
        write_audit_event(
            cosmos_service,
            event_type="verification_email_sent",
            owner_user_id=str(created_user.get("id", "")),
            actor_user_id=str(created_user.get("id", "")),
            actor_email=normalized_email,
            metadata={
                "recipient_email": normalized_email,
                "message_id": verification_email_result.message_id,
            },
        )
    elif verification_email_result.failed:
        write_audit_event(
            cosmos_service,
            event_type="email_send_failed",
            owner_user_id=str(created_user.get("id", "")),
            actor_user_id=str(created_user.get("id", "")),
            actor_email=normalized_email,
            metadata={
                "email_kind": "verification",
                "recipient_email": normalized_email,
                "error": verification_email_result.error,
            },
        )
    return AuthRegisterResponse(
        message="Account created. Verify your email before signing in.",
        user_id=str(created_user.get("id")),
        email=str(created_user.get("email", normalized_email)),
        username=str(created_user.get("username", normalized_username)),
        email_verification_required=auth_service.should_require_email_verification(),
        verification_url=verification_url,
        verification_token=verification_token if _should_expose_verification_token() else None,
    )


@app.post("/auth/verify-email")
def verify_email(payload: EmailVerificationRequest, request: Request):
    cosmos_service = get_cosmos_service(request)
    auth_service = get_auth_service(request)

    token = payload.token.strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token is required.",
        )

    token_hash = auth_service.hash_verification_token(token)
    try:
        user_item = cosmos_service.get_user_by_verification_token_hash(token_hash)
    except Exception:
        logger.exception("Failed to resolve user for verification token.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify email.",
        ) from None

    if user_item is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token.",
        )

    expiry_raw = str(user_item.get("verification_token_expires_at", "")).strip()
    expiry_time = _parse_iso_datetime(expiry_raw)
    now = datetime.now(timezone.utc)
    if expiry_time is None or expiry_time <= now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token.",
        )

    try:
        updated_user = cosmos_service.update_user(
            str(user_item.get("id")),
            {
                "is_email_verified": True,
                "verification_token_hash": None,
                "verification_token_expires_at": None,
            },
        )
    except Exception:
        logger.exception("Failed to mark email as verified for user id=%s", user_item.get("id"))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify email.",
        ) from None

    if updated_user is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify email.",
        )

    return {
        "message": "Email verified successfully.",
        "user_id": str(updated_user.get("id")),
        "email": str(updated_user.get("email", "")),
    }


@app.post("/auth/login", response_model=AuthTokenResponse)
def login_account(payload: AuthLoginRequest, request: Request) -> AuthTokenResponse:
    cosmos_service = get_cosmos_service(request)
    auth_service = get_auth_service(request)

    normalized_email = auth_service.normalize_email(payload.email)
    if not is_valid_email(normalized_email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid email is required.",
        )

    enforce_login_rate_limit(request, normalized_email)

    try:
        user_item = cosmos_service.get_user_by_email(normalized_email)
    except Exception:
        logger.exception("Failed to fetch user during login email=%s", normalized_email)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to authenticate user.",
        ) from None

    if user_item is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    password_hash = str(user_item.get("password_hash", ""))
    if not auth_service.verify_password(payload.password, password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    email_verified = bool(user_item.get("is_email_verified", False))
    if auth_service.should_require_email_verification() and not email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email verification is required before signing in.",
        )

    token_payload = auth_service.issue_access_token(user_item)
    write_audit_event(
        cosmos_service,
        event_type="login",
        owner_user_id=str(user_item.get("id", "")),
        actor_user_id=str(user_item.get("id", "")),
        actor_email=normalized_email,
        metadata={"email_verified": email_verified},
    )
    return AuthTokenResponse(
        access_token=token_payload["access_token"],
        token_type=token_payload["token_type"],
        expires_at=token_payload["expires_at"],
        user_id=str(user_item.get("id")),
        email=str(user_item.get("email", "")),
        email_verified=email_verified,
    )


@app.get("/auth/me", response_model=AuthMeResponse)
def get_current_user_profile(current_user: Dict[str, Any] = Depends(get_current_user)) -> AuthMeResponse:
    return AuthMeResponse(
        user_id=str(current_user.get("id", "")),
        email=str(current_user.get("email", "")),
        email_verified=bool(current_user.get("is_email_verified", False)),
        username=str(current_user.get("username", "")),
        full_name=str(current_user.get("full_name", "")),
        birth_date=str(current_user.get("birth_date", "")),
        display_name_preference=resolve_display_name_preference(
            str(current_user.get("display_name_preference", "username"))
        ),
    )


@app.patch("/auth/me", response_model=AuthMeResponse)
def update_current_user_profile(
    payload: AuthProfileUpdateRequest,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> AuthMeResponse:
    cosmos_service = get_cosmos_service(request)
    user_id = str(current_user.get("id", "")).strip()

    try:
        normalized_username = normalize_username(payload.username)
        normalized_full_name = normalize_full_name(payload.full_name)
        normalized_birth_date = validate_birth_date(payload.birth_date)
        display_name_preference = resolve_display_name_preference(payload.display_name_preference)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        existing_username_owner = cosmos_service.get_user_by_username(normalized_username)
    except Exception:
        logger.exception("Failed to validate username uniqueness for update. user_id=%s", user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update profile.",
        ) from None

    if existing_username_owner is not None and str(existing_username_owner.get("id")) != user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this username already exists.",
        )

    try:
        updated_user = cosmos_service.update_user(
            user_id,
            {
                "username": normalized_username,
                "full_name": normalized_full_name,
                "birth_date": normalized_birth_date,
                "display_name_preference": display_name_preference,
            },
        )
    except Exception:
        logger.exception("Failed to update profile. user_id=%s", user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update profile.",
        ) from None

    if updated_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account was not found.",
        )

    return AuthMeResponse(
        user_id=user_id,
        email=str(updated_user.get("email", "")),
        email_verified=bool(updated_user.get("is_email_verified", False)),
        username=str(updated_user.get("username", "")),
        full_name=str(updated_user.get("full_name", "")),
        birth_date=str(updated_user.get("birth_date", "")),
        display_name_preference=resolve_display_name_preference(
            str(updated_user.get("display_name_preference", "username"))
        ),
    )


@app.post("/auth/change-password")
def change_password(
    payload: AuthChangePasswordRequest,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    cosmos_service = get_cosmos_service(request)
    auth_service = get_auth_service(request)
    user_id = str(current_user.get("id", "")).strip()
    password_hash = str(current_user.get("password_hash", ""))

    if not auth_service.verify_password(payload.current_password, password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        new_password_hash = auth_service.hash_password(payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        updated_user = cosmos_service.update_user(user_id, {"password_hash": new_password_hash})
    except Exception:
        logger.exception("Failed to change password. user_id=%s", user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to change password.",
        ) from None

    if updated_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account was not found.",
        )

    return {"message": "Password updated successfully."}


def _delete_vault_assets(blob_service: BlobService, vault_item: Dict[str, Any]) -> None:
    files = vault_item.get("files", [])
    if not isinstance(files, list):
        files = []

    for file_item in files:
        if not isinstance(file_item, dict):
            continue
        container_name = file_item.get("container_name")
        blob_name = file_item.get("blob_name")
        if container_name and blob_name:
            blob_service.delete_blob(container_name=container_name, blob_name=blob_name)

    delivery_container_name = vault_item.get("delivery_container_name")
    delivery_blob_name = vault_item.get("delivery_blob_name")
    if delivery_container_name and delivery_blob_name:
        blob_service.delete_blob(
            container_name=str(delivery_container_name),
            blob_name=str(delivery_blob_name),
        )


@app.delete("/auth/me")
def delete_current_user_account(
    payload: AuthDeleteAccountRequest,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    cosmos_service = get_cosmos_service(request)
    blob_service = get_blob_service(request)
    auth_service = get_auth_service(request)
    user_id = str(current_user.get("id", "")).strip()
    email = str(current_user.get("email", "")).strip().lower()

    if not auth_service.verify_password(payload.password, str(current_user.get("password_hash", ""))):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Password is incorrect.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        for vault_item in cosmos_service.list_vaults(user_id=user_id):
            _delete_vault_assets(blob_service, vault_item)
            cosmos_service.delete_vault(str(vault_item.get("id", "")))

        for vault_item in cosmos_service.list_vaults():
            internal_vault_id = str(vault_item.get("id", "")).strip()
            recipients = vault_item.get("recipients", [])
            if isinstance(recipients, list) and any(
                isinstance(recipient, str) and recipient.strip().lower() == email
                for recipient in recipients
            ):
                vault_item = cosmos_service.remove_recipient_from_vault(internal_vault_id, email) or vault_item

            activation_requests = vault_item.get("activation_requests", [])
            if isinstance(activation_requests, list) and any(
                isinstance(activation_request, dict)
                and str(activation_request.get("recipient_email", "")).strip().lower() == email
                for activation_request in activation_requests
            ):
                cosmos_service.remove_activation_request(internal_vault_id, email)

        deleted = cosmos_service.delete_user(user_id)
    except Exception:
        logger.exception("Failed to delete account. user_id=%s", user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete account.",
        ) from None

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account was not found.",
        )

    return {"message": "Account deleted successfully."}


@app.post("/vaults", response_model=VaultResponse, status_code=status.HTTP_201_CREATED)
def create_vault(
    vault: VaultCreate,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> VaultResponse:
    cosmos_service = get_cosmos_service(request)
    vault_key_service = get_vault_key_service(request)
    vault_payload = vault.model_dump(exclude_unset=True)
    vault_id = str(uuid4())
    vault_payload["id"] = vault_id
    vault_payload["short_id"] = generate_unique_short_id(cosmos_service)
    vault_payload["user_id"] = str(current_user.get("id", ""))
    vault_payload["status"] = "active"
    vault_payload["owner_message"] = (
        str(vault_payload.get("owner_message", "")).strip() or None
    )

    try:
        recipients = vault_payload.get("recipients", [])
        if recipients is not None:
            vault_payload["recipients"] = normalize_recipients(recipients)
        vault_payload.update(vault_key_service.ensure_vault_key(vault_id))
        created_item = cosmos_service.create_vault(vault_payload)
        write_audit_event(
            cosmos_service,
            event_type="vault_created",
            owner_user_id=str(current_user.get("id", "")),
            vault_id=vault_id,
            actor_user_id=str(current_user.get("id", "")),
            actor_email=str(current_user.get("email", "")).strip().lower() or None,
            metadata={"recipient_count": len(created_item.get("recipients", []))},
        )
        return VaultResponse(**build_public_vault_payload(created_item))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception:
        logger.exception("Unhandled error while creating vault.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create vault.",
        ) from None


@app.get("/vaults", response_model=List[VaultResponse])
def list_vaults(
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> List[VaultResponse]:
    cosmos_service = get_cosmos_service(request)
    user_id = str(current_user.get("id", ""))

    try:
        vault_items = cosmos_service.list_vaults(user_id=user_id)
        return [VaultResponse(**build_public_vault_payload(vault_item)) for vault_item in vault_items]
    except Exception:
        logger.exception("Unhandled error while listing vaults.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list vaults.",
        ) from None


def _build_recipient_vault_summary(
    vault_item: Dict[str, Any], recipient_email: str
) -> RecipientVaultSummary:
    normalized_email = recipient_email.strip().lower()
    activation_requests = vault_item.get("activation_requests", [])
    if not isinstance(activation_requests, list):
        activation_requests = []

    has_requested = any(
        isinstance(request_item, dict)
        and str(request_item.get("recipient_email", "")).strip().lower()
        == normalized_email
        for request_item in activation_requests
    )

    try:
        threshold_value = max(1, int(vault_item.get("activation_threshold", 1)))
    except (TypeError, ValueError):
        threshold_value = 1

    try:
        grace_period_days_value = max(0, int(vault_item.get("grace_period_days", 0)))
    except (TypeError, ValueError):
        grace_period_days_value = 0

    owner_display_name = build_user_display_name(vault_item.get("owner_profile"))
    return RecipientVaultSummary(
        id=str(vault_item.get("short_id", "")).strip() or str(vault_item.get("id", "")),
        name=str(vault_item.get("name", "")),
        owner_display_name=owner_display_name,
        status=str(vault_item.get("status", "active")),
        grace_period_days=grace_period_days_value,
        activation_threshold=threshold_value,
        activation_requests_count=len(activation_requests),
        has_requested_activation=has_requested,
        grace_period_expires_at=vault_item.get("grace_period_expires_at"),
        delivered_at=vault_item.get("delivered_at"),
        delivery_available=bool(vault_item.get("delivery_blob_name")),
    )


@app.get("/vaults/incoming", response_model=List[RecipientVaultSummary])
def list_incoming_vaults(
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> List[RecipientVaultSummary]:
    cosmos_service = get_cosmos_service(request)
    recipient_email = str(current_user.get("email", "")).strip().lower()

    if not recipient_email:
        return []

    try:
        vault_items = cosmos_service.list_vaults_for_recipient(recipient_email)
        owner_ids = {
            str(vault_item.get("user_id", "")).strip()
            for vault_item in vault_items
            if str(vault_item.get("user_id", "")).strip()
        }
        owner_profiles = {
            owner_id: cosmos_service.get_user_by_id(owner_id)
            for owner_id in owner_ids
        }
    except Exception:
        logger.exception(
            "Unhandled error while listing incoming vaults for recipient=%s",
            recipient_email,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list incoming vaults.",
        ) from None

    return [
        _build_recipient_vault_summary(
            {**vault_item, "owner_profile": owner_profiles.get(str(vault_item.get("user_id", "")).strip())},
            recipient_email,
        )
        for vault_item in vault_items
    ]


@app.get("/vaults/{vault_id}", response_model=VaultResponse)
def get_vault(
    vault_id: str,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> VaultResponse:
    cosmos_service = get_cosmos_service(request)
    vault_item = get_owned_vault_or_404(
        cosmos_service=cosmos_service,
        public_vault_id=vault_id,
        user_id=str(current_user.get("id", "")),
    )

    return VaultResponse(**build_public_vault_payload(vault_item))


@app.patch("/vaults/{vault_id}", response_model=VaultResponse)
def update_vault(
    vault_id: str,
    payload: VaultUpdate,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> VaultResponse:
    cosmos_service = get_cosmos_service(request)
    update_data = payload.model_dump(exclude_unset=True)
    existing_vault = get_owned_vault_or_404(
        cosmos_service=cosmos_service,
        public_vault_id=vault_id,
        user_id=str(current_user.get("id", "")),
    )
    ensure_vault_is_mutable(existing_vault)
    internal_vault_id = str(existing_vault.get("id", "")).strip()

    try:
        if "status" in update_data and update_data["status"] is not None:
            raise ValueError(
                "Vault status is managed by the delivery pipeline and cannot be updated manually."
            )
        if "recipients" in update_data and update_data["recipients"] is not None:
            update_data["recipients"] = normalize_recipients(update_data["recipients"])
        if "owner_message" in update_data:
            update_data["owner_message"] = str(update_data.get("owner_message", "")).strip() or None

        updated_vault = cosmos_service.update_vault(internal_vault_id, update_data)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception:
        logger.exception("Unhandled error while updating vault id=%s", vault_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update vault.",
        ) from None

    if updated_vault is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vault not found.",
        )

    return VaultResponse(**build_public_vault_payload(updated_vault))


@app.delete("/vaults/{vault_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vault(
    vault_id: str,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> None:
    cosmos_service = get_cosmos_service(request)
    blob_service = get_blob_service(request)
    vault_item = get_owned_vault_or_404(
        cosmos_service=cosmos_service,
        public_vault_id=vault_id,
        user_id=str(current_user.get("id", "")),
    )
    internal_vault_id = str(vault_item.get("id", "")).strip()

    try:
        _delete_vault_assets(blob_service, vault_item)
        deleted = cosmos_service.delete_vault(internal_vault_id)
    except Exception:
        logger.exception("Unhandled error while deleting vault id=%s", vault_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete vault.",
        ) from None

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vault not found.",
        )

    return None


@app.post("/vaults/{vault_id}/check-in", response_model=VaultResponse)
def check_in_vault(
    vault_id: str,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> VaultResponse:
    cosmos_service = get_cosmos_service(request)
    existing_vault = get_owned_vault_or_404(
        cosmos_service=cosmos_service,
        public_vault_id=vault_id,
        user_id=str(current_user.get("id", "")),
    )
    ensure_vault_is_mutable(existing_vault)
    internal_vault_id = str(existing_vault.get("id", "")).strip()

    try:
        updated_vault = cosmos_service.check_in_vault(internal_vault_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception:
        logger.exception("Unhandled error while checking in vault id=%s", vault_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check in vault.",
        ) from None

    if updated_vault is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vault not found.",
        )

    write_audit_event(
        cosmos_service,
        event_type="check_in",
        owner_user_id=str(current_user.get("id", "")),
        vault_id=internal_vault_id,
        actor_user_id=str(current_user.get("id", "")),
        actor_email=str(current_user.get("email", "")).strip().lower() or None,
    )

    return VaultResponse(**build_public_vault_payload(updated_vault))


@app.get(
    "/vaults/{vault_id}/activation-summary",
    response_model=RecipientVaultSummary,
)
def get_vault_activation_summary(
    vault_id: str,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> RecipientVaultSummary:
    cosmos_service = get_cosmos_service(request)
    recipient_email = str(current_user.get("email", "")).strip().lower()

    try:
        vault_item = resolve_vault_by_public_id(cosmos_service, vault_id)
        owner_profile = (
            None
            if vault_item is None
            else cosmos_service.get_user_by_id(str(vault_item.get("user_id", "")).strip())
        )
    except Exception:
        logger.exception(
            "Unhandled error while reading vault for activation summary id=%s",
            vault_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve vault.",
        ) from None

    if vault_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vault not found.",
        )

    if not _is_vault_recipient(vault_item, recipient_email):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vault not found.",
        )

    return _build_recipient_vault_summary(
        {**vault_item, "owner_profile": owner_profile},
        recipient_email,
    )


@app.get("/vaults/{vault_id}/delivery-package")
def download_delivery_package(
    vault_id: str,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    cosmos_service = get_cosmos_service(request)
    blob_service = get_blob_service(request)

    try:
        vault_item = resolve_vault_by_public_id(cosmos_service, vault_id)
    except Exception:
        logger.exception("Unhandled error while reading delivery package for vault id=%s", vault_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve delivery package.",
        ) from None

    if vault_item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vault not found.")

    current_user_id = str(current_user.get("id", "")).strip()
    current_email = str(current_user.get("email", "")).strip().lower()
    is_owner = str(vault_item.get("user_id", "")).strip() == current_user_id
    is_recipient = _is_vault_recipient(vault_item, current_email)
    if not is_owner and not is_recipient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vault not found.")

    delivery_container_name = str(vault_item.get("delivery_container_name", "")).strip()
    delivery_blob_name = str(vault_item.get("delivery_blob_name", "")).strip()
    if not delivery_container_name or not delivery_blob_name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The delivery package is not available yet.",
        )

    try:
        payload = blob_service.download_blob_bytes(
            container_name=delivery_container_name,
            blob_name=delivery_blob_name,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The delivery package could not be found.",
        ) from None
    except Exception:
        logger.exception("Failed to download delivery package for vault id=%s", vault_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to download delivery package.",
        ) from None

    file_name = "last-writes-delivery.zip"
    return Response(
        content=payload,
        media_type="application/zip",
        headers=_build_attachment_headers(file_name),
    )


@app.post(
    "/vaults/{vault_id}/activation-requests",
    response_model=RecipientVaultSummary,
    status_code=status.HTTP_201_CREATED,
)
def submit_activation_request(
    vault_id: str,
    payload: ActivationRequestCreate,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> RecipientVaultSummary:
    cosmos_service = get_cosmos_service(request)
    recipient_email = str(current_user.get("email", "")).strip().lower()

    if not recipient_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A verified email is required.",
        )

    try:
        existing_vault = resolve_vault_by_public_id(cosmos_service, vault_id)
    except Exception:
        logger.exception(
            "Unhandled error while reading vault before activation request. vault_id=%s",
            vault_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit activation request.",
        ) from None

    internal_vault_id = "" if existing_vault is None else str(existing_vault.get("id", "")).strip()

    try:
        updated_vault, outcome = cosmos_service.add_activation_request(
            vault_id=internal_vault_id,
            recipient_email=recipient_email,
            reason=payload.reason,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception(
            "Unhandled error while submitting activation request. vault_id=%s",
            vault_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit activation request.",
        ) from None

    if outcome == "not_found" or updated_vault is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vault not found.",
        )

    if outcome == "terminal":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This vault is no longer accepting activation requests.",
        )

    owner_user_id = str(updated_vault.get("user_id", "")).strip()
    previous_status = str((existing_vault or {}).get("status", "")).strip().lower()
    write_audit_event(
        cosmos_service,
        event_type="activation_requested",
        owner_user_id=owner_user_id,
        vault_id=internal_vault_id,
        actor_user_id=str(current_user.get("id", "")),
        actor_email=recipient_email,
        metadata={"outcome": outcome},
    )
    if (
        owner_user_id
        and previous_status != "grace_period"
        and str(updated_vault.get("status", "")).strip().lower() == "grace_period"
        and str(updated_vault.get("grace_period_started_at", "")).strip()
    ):
        write_audit_event(
            cosmos_service,
            event_type="grace_period_started",
            owner_user_id=owner_user_id,
            vault_id=internal_vault_id,
            actor_user_id=str(current_user.get("id", "")),
            actor_email=recipient_email,
            metadata={
                "grace_period_started_at": updated_vault.get("grace_period_started_at"),
                "grace_period_expires_at": updated_vault.get("grace_period_expires_at"),
            },
        )

    owner_profile = cosmos_service.get_user_by_id(owner_user_id) if owner_user_id else None
    return _build_recipient_vault_summary(
        {**updated_vault, "owner_profile": owner_profile},
        recipient_email,
    )


@app.delete(
    "/vaults/{vault_id}/activation-requests",
    response_model=RecipientVaultSummary,
)
def withdraw_activation_request(
    vault_id: str,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> RecipientVaultSummary:
    cosmos_service = get_cosmos_service(request)
    recipient_email = str(current_user.get("email", "")).strip().lower()

    try:
        existing_vault = resolve_vault_by_public_id(cosmos_service, vault_id)
    except Exception:
        logger.exception("Unhandled error while reading vault id=%s", vault_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve vault.",
        ) from None

    if existing_vault is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vault not found.",
        )

    recipients = existing_vault.get("recipients", [])
    if not isinstance(recipients, list):
        recipients = []

    is_recipient = any(
        isinstance(candidate, str)
        and candidate.strip().lower() == recipient_email
        for candidate in recipients
    )
    if not is_recipient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vault not found.",
        )

    internal_vault_id = str(existing_vault.get("id", "")).strip()

    try:
        updated_vault = cosmos_service.remove_activation_request(
            vault_id=internal_vault_id,
            recipient_email=recipient_email,
        )
    except Exception:
        logger.exception(
            "Unhandled error while withdrawing activation request. vault_id=%s",
            vault_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to withdraw activation request.",
        ) from None

    if updated_vault is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vault not found.",
        )

    owner_profile = cosmos_service.get_user_by_id(str(updated_vault.get("user_id", "")).strip())
    return _build_recipient_vault_summary(
        {**updated_vault, "owner_profile": owner_profile},
        recipient_email,
    )


@app.get("/vaults/{vault_id}/recipients")
def list_vault_recipients(
    vault_id: str,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    cosmos_service = get_cosmos_service(request)
    vault_item = get_owned_vault_or_404(
        cosmos_service=cosmos_service,
        public_vault_id=vault_id,
        user_id=str(current_user.get("id", "")),
    )

    recipients = vault_item.get("recipients", [])
    if not isinstance(recipients, list):
        recipients = []

    return {
        "vault_id": str(vault_item.get("short_id", "")).strip() or vault_id,
        "recipients": recipients,
    }


@app.post("/vaults/{vault_id}/recipients")
def add_vault_recipient(
    vault_id: str,
    payload: RecipientCreateRequest,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    cosmos_service = get_cosmos_service(request)
    email_service = get_email_service(request)
    recipient_email = payload.email.strip().lower()
    existing_vault = get_owned_vault_or_404(
        cosmos_service=cosmos_service,
        public_vault_id=vault_id,
        user_id=str(current_user.get("id", "")),
    )
    ensure_vault_is_mutable(existing_vault)
    internal_vault_id = str(existing_vault.get("id", "")).strip()

    if not is_valid_email(recipient_email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid recipient email is required.",
        )

    try:
        updated_vault = cosmos_service.add_recipient_to_vault(internal_vault_id, recipient_email)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception(
            "Unhandled error while adding recipient. vault_id=%s",
            vault_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to add recipient.",
        ) from None

    if updated_vault is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vault not found.",
        )

    recipients = updated_vault.get("recipients", [])
    if not isinstance(recipients, list):
        recipients = []

    owner_user_id = str(current_user.get("id", ""))
    owner_email = str(current_user.get("email", "")).strip().lower()
    invite_result = email_service.send_recipient_invited_email(
        recipient=recipient_email,
        public_vault_id=str(updated_vault.get("short_id", "")).strip() or vault_id,
        vault_name=str(updated_vault.get("name", "Unnamed Vault")).strip() or "Unnamed Vault",
        owner_label=build_user_display_name(current_user) or owner_email or "unknown",
    )
    if invite_result.sent:
        write_audit_event(
            cosmos_service,
            event_type="recipient_invited_email_sent",
            owner_user_id=owner_user_id,
            vault_id=internal_vault_id,
            actor_user_id=owner_user_id,
            actor_email=owner_email,
            metadata={
                "recipient_email": recipient_email,
                "message_id": invite_result.message_id,
            },
        )
    elif invite_result.failed:
        write_audit_event(
            cosmos_service,
            event_type="email_send_failed",
            owner_user_id=owner_user_id,
            vault_id=internal_vault_id,
            actor_user_id=owner_user_id,
            actor_email=owner_email,
            metadata={
                "email_kind": "recipient_invite",
                "recipient_email": recipient_email,
                "error": invite_result.error,
            },
        )

    return {
        "vault_id": str(updated_vault.get("short_id", "")).strip() or vault_id,
        "recipients": recipients,
    }


@app.delete("/vaults/{vault_id}/recipients/{recipient_email:path}")
def delete_vault_recipient(
    vault_id: str,
    recipient_email: str,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    cosmos_service = get_cosmos_service(request)
    normalized_email = recipient_email.strip().lower()
    existing_vault = get_owned_vault_or_404(
        cosmos_service=cosmos_service,
        public_vault_id=vault_id,
        user_id=str(current_user.get("id", "")),
    )
    ensure_vault_is_mutable(existing_vault)
    internal_vault_id = str(existing_vault.get("id", "")).strip()

    if not is_valid_email(normalized_email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid recipient email is required.",
        )

    try:
        updated_vault = cosmos_service.remove_recipient_from_vault(internal_vault_id, normalized_email)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception(
            "Unhandled error while deleting recipient. vault_id=%s",
            vault_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete recipient.",
        ) from None

    if updated_vault is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vault not found.",
        )

    recipients = updated_vault.get("recipients", [])
    if not isinstance(recipients, list):
        recipients = []

    return {
        "vault_id": str(updated_vault.get("short_id", "")).strip() or vault_id,
        "recipients": recipients,
    }


@app.get("/vaults/{vault_id}/files")
def list_vault_files(
    vault_id: str,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    cosmos_service = get_cosmos_service(request)
    vault_item = get_owned_vault_or_404(
        cosmos_service=cosmos_service,
        public_vault_id=vault_id,
        user_id=str(current_user.get("id", "")),
    )
    files = vault_item.get("files", [])
    if not isinstance(files, list):
        files = []

    return {
        "vault_id": str(vault_item.get("short_id", "")).strip() or vault_id,
        "files": files,
    }


@app.get("/vaults/{vault_id}/audit", response_model=List[AuditLogEntry])
def list_vault_audit_logs(
    vault_id: str,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> List[AuditLogEntry]:
    cosmos_service = get_cosmos_service(request)
    existing_vault = get_owned_vault_or_404(
        cosmos_service=cosmos_service,
        public_vault_id=vault_id,
        user_id=str(current_user.get("id", "")),
    )
    internal_vault_id = str(existing_vault.get("id", "")).strip()

    try:
        audit_items = cosmos_service.list_vault_audit_events(
            vault_id=internal_vault_id,
            owner_user_id=str(current_user.get("id", "")),
        )
    except Exception:
        logger.exception("Unhandled error while listing audit logs for vault id=%s", vault_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve audit log.",
        ) from None

    return [AuditLogEntry(**audit_item) for audit_item in audit_items]


@app.get("/vaults/{vault_id}/files/{file_id}/download")
def download_vault_file(
    vault_id: str,
    file_id: str,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    cosmos_service = get_cosmos_service(request)
    blob_service = get_blob_service(request)
    vault_key_service = get_vault_key_service(request)
    vault_item = get_owned_vault_or_404(
        cosmos_service=cosmos_service,
        public_vault_id=vault_id,
        user_id=str(current_user.get("id", "")),
    )

    file_metadata = get_vault_file_metadata(vault_item, file_id)
    if file_metadata is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found for vault.",
        )

    container_name = file_metadata.get("container_name")
    blob_name = file_metadata.get("blob_name")
    if not container_name or not blob_name:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="File metadata is missing blob location details.",
        )

    try:
        payload = _download_vault_file_bytes(
            blob_service=blob_service,
            vault_key_service=vault_key_service,
            file_metadata=file_metadata,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Blob not found for requested file.",
        ) from None
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception(
            "Unhandled error while preparing file download. vault_id=%s file_id=%s",
            vault_id,
            file_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to download file.",
        ) from None

    file_name = str(file_metadata.get("file_name", "")).strip() or f"{file_id}.bin"
    media_type = str(file_metadata.get("content_type", "")).strip() or "application/octet-stream"
    return Response(
        content=payload,
        media_type=media_type,
        headers=_build_attachment_headers(file_name),
    )


@app.delete("/vaults/{vault_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vault_file(
    vault_id: str,
    file_id: str,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> None:
    cosmos_service = get_cosmos_service(request)
    blob_service = get_blob_service(request)
    vault_item = get_owned_vault_or_404(
        cosmos_service=cosmos_service,
        public_vault_id=vault_id,
        user_id=str(current_user.get("id", "")),
    )
    ensure_vault_is_mutable(vault_item)
    internal_vault_id = str(vault_item.get("id", "")).strip()

    file_metadata = get_vault_file_metadata(vault_item, file_id)
    if file_metadata is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found for vault.",
        )

    container_name = file_metadata.get("container_name")
    blob_name = file_metadata.get("blob_name")
    if not container_name or not blob_name:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="File metadata is missing blob location details.",
        )

    try:
        blob_service.delete_blob(container_name=container_name, blob_name=blob_name)
        updated_vault = cosmos_service.remove_file_from_vault(internal_vault_id, file_id)
    except Exception:
        logger.exception(
            "Unhandled error while deleting file. vault_id=%s file_id=%s",
            vault_id,
            file_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete file.",
        ) from None

    if updated_vault is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vault not found.",
        )

    return None


@app.get("/local-downloads/{container_name}/{blob_name:path}")
def download_local_blob(container_name: str, blob_name: str, request: Request):
    blob_service = get_blob_service(request)
    if not getattr(blob_service, "is_local", False):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Local download route is only available in local development mode.",
        )

    try:
        file_path = blob_service.get_local_file_path(
            container_name=container_name,
            blob_name=blob_name,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Blob not found for requested file.",
        ) from None

    return FileResponse(path=file_path, filename=file_path.name)


@app.post("/vaults/{vault_id}/files", status_code=status.HTTP_201_CREATED)
async def upload_vault_file(
    vault_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    cosmos_service = get_cosmos_service(request)
    blob_service = get_blob_service(request)
    vault_key_service = get_vault_key_service(request)

    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File name is required.",
        )

    sanitized_file_name = sanitize_filename(file.filename)

    vault_item = get_owned_vault_or_404(
        cosmos_service=cosmos_service,
        public_vault_id=vault_id,
        user_id=str(current_user.get("id", "")),
    )
    ensure_vault_is_mutable(vault_item)
    internal_vault_id = str(vault_item.get("id", "")).strip()

    uploaded_file_metadata = None
    try:
        vault_item = ensure_vault_key_metadata(
            cosmos_service=cosmos_service,
            vault_key_service=vault_key_service,
            vault_item=vault_item,
        )

        plaintext_bytes = await file.read()
        validated_content_type = validate_upload(file, plaintext_bytes, sanitized_file_name)
        encryption_payload = encrypt_file_bytes(
            plaintext_bytes,
            vault_item.get("public_jwk", {}),
        )

        uploaded_blob_metadata = blob_service.upload_bytes(
            vault_id=internal_vault_id,
            payload=encryption_payload["ciphertext"],
            file_name=sanitized_file_name,
            content_type=validated_content_type,
            blob_content_type="application/octet-stream",
        )
        uploaded_file_metadata = dict(uploaded_blob_metadata)
        uploaded_file_metadata.update(encryption_payload["metadata"])
        uploaded_file_metadata["content_type"] = validated_content_type
        uploaded_file_metadata["blob_content_type"] = "application/octet-stream"
        uploaded_file_metadata["size_bytes"] = len(plaintext_bytes)
        uploaded_file_metadata["ciphertext_size_bytes"] = len(encryption_payload["ciphertext"])
        uploaded_file_metadata["key_kid"] = str(vault_item.get("key_kid", "")).strip()
        uploaded_file_metadata["key_version"] = str(vault_item.get("key_version", "")).strip()

        existing_files = vault_item.get("files", [])
        if not isinstance(existing_files, list):
            existing_files = []
        updated_files = existing_files + [uploaded_file_metadata]

        updated_vault = cosmos_service.update_vault(internal_vault_id, {"files": updated_files})
        if updated_vault is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vault not found during metadata update.",
            )

        return {
            "vault_id": str(updated_vault.get("short_id", "")).strip() or vault_id,
            "file": uploaded_file_metadata,
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("File upload pipeline failed for vault id=%s", vault_id)
        if uploaded_file_metadata is not None:
            try:
                blob_service.delete_blob(
                    container_name=uploaded_file_metadata["container_name"],
                    blob_name=uploaded_file_metadata["blob_name"],
                )
            except Exception:
                logger.exception(
                    "Rollback failed after metadata persistence error for vault id=%s",
                    vault_id,
                )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload file.",
        ) from None
    finally:
        await file.close()
