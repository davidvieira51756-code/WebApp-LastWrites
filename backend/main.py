from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
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
        AuthLoginRequest,
        AuthMeResponse,
        AuthRegisterRequest,
        AuthRegisterResponse,
        AuthTokenResponse,
        EmailVerificationRequest,
    )
    from backend.models.vault import (
        ActivationRequestCreate,
        RecipientVaultSummary,
        VaultCreate,
        VaultResponse,
        VaultUpdate,
    )
    from backend.services.auth_service import AuthService
    from backend.services.blob_service import BlobService
    from backend.services.cosmos_service import CosmosService
    from backend.services.file_crypto_service import decrypt_file_bytes, encrypt_file_bytes, sha256_hexdigest
    from backend.services.keyvault_service import KeyVaultService
    from backend.services.local_blob_service import LocalBlobService
    from backend.services.local_cosmos_service import LocalCosmosService
    from backend.services.vault_key_service import AzureVaultKeyService, LocalVaultKeyService
except ModuleNotFoundError:
    from models.auth import (
        AuthLoginRequest,
        AuthMeResponse,
        AuthRegisterRequest,
        AuthRegisterResponse,
        AuthTokenResponse,
        EmailVerificationRequest,
    )
    from models.vault import (
        ActivationRequestCreate,
        RecipientVaultSummary,
        VaultCreate,
        VaultResponse,
        VaultUpdate,
    )
    from services.auth_service import AuthService
    from services.blob_service import BlobService
    from services.cosmos_service import CosmosService
    from services.file_crypto_service import decrypt_file_bytes, encrypt_file_bytes, sha256_hexdigest
    from services.keyvault_service import KeyVaultService
    from services.local_blob_service import LocalBlobService
    from services.local_cosmos_service import LocalCosmosService
    from services.vault_key_service import AzureVaultKeyService, LocalVaultKeyService

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

EMAIL_ADDRESS_REGEX = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$"
)
auth_bearer = HTTPBearer(auto_error=False)


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


def _build_attachment_headers(file_name: str) -> Dict[str, str]:
    normalized_name = file_name.strip() or "download.bin"
    fallback_name = normalized_name.encode("ascii", errors="ignore").decode("ascii") or "download.bin"
    fallback_name = fallback_name.replace('"', "")
    encoded_name = quote(normalized_name)
    return {
        "Content-Disposition": (
            f'attachment; filename="{fallback_name}"; filename*=UTF-8\'\'{encoded_name}'
        )
    }


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
    vault_id: str,
    user_id: str,
) -> Dict[str, Any]:
    try:
        vault_item = cosmos_service.get_vault_by_id(vault_id)
    except Exception:
        logger.exception("Unhandled error while retrieving vault id=%s", vault_id)
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
        app.state.vault_key_service = vault_key_service
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

    normalized_email = auth_service.normalize_email(payload.email)
    if not is_valid_email(normalized_email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid email is required.",
        )

    try:
        password_hash = auth_service.hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    verification_payload = auth_service.issue_email_verification()
    user_document = {
        "email": normalized_email,
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
    return AuthRegisterResponse(
        message="Account created. Verify your email before signing in.",
        user_id=str(created_user.get("id")),
        email=str(created_user.get("email", normalized_email)),
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
    )


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
        return VaultResponse(**created_item)
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
        return [VaultResponse(**vault_item) for vault_item in vault_items]
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

    return RecipientVaultSummary(
        id=str(vault_item.get("id", "")),
        name=str(vault_item.get("name", "")),
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
        _build_recipient_vault_summary(vault_item, recipient_email)
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
        vault_id=vault_id,
        user_id=str(current_user.get("id", "")),
    )

    return VaultResponse(**vault_item)


@app.patch("/vaults/{vault_id}", response_model=VaultResponse)
def update_vault(
    vault_id: str,
    payload: VaultUpdate,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> VaultResponse:
    cosmos_service = get_cosmos_service(request)
    update_data = payload.model_dump(exclude_unset=True)
    get_owned_vault_or_404(
        cosmos_service=cosmos_service,
        vault_id=vault_id,
        user_id=str(current_user.get("id", "")),
    )

    try:
        if "status" in update_data and update_data["status"] is not None:
            raise ValueError(
                "Vault status is managed by the delivery pipeline and cannot be updated manually."
            )
        if "recipients" in update_data and update_data["recipients"] is not None:
            update_data["recipients"] = normalize_recipients(update_data["recipients"])
        if "owner_message" in update_data:
            update_data["owner_message"] = str(update_data.get("owner_message", "")).strip() or None

        updated_vault = cosmos_service.update_vault(vault_id, update_data)
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

    return VaultResponse(**updated_vault)


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
        vault_id=vault_id,
        user_id=str(current_user.get("id", "")),
    )

    files = vault_item.get("files", [])
    if not isinstance(files, list):
        files = []

    try:
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

        deleted = cosmos_service.delete_vault(vault_id)
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
    get_owned_vault_or_404(
        cosmos_service=cosmos_service,
        vault_id=vault_id,
        user_id=str(current_user.get("id", "")),
    )

    try:
        updated_vault = cosmos_service.check_in_vault(vault_id)
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

    return VaultResponse(**updated_vault)


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
        vault_item = cosmos_service.get_vault_by_id(vault_id)
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

    return _build_recipient_vault_summary(vault_item, recipient_email)


@app.get("/vaults/{vault_id}/delivery-package")
def download_delivery_package(
    vault_id: str,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    cosmos_service = get_cosmos_service(request)
    blob_service = get_blob_service(request)

    try:
        vault_item = cosmos_service.get_vault_by_id(vault_id)
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

    file_name = str(vault_item.get("delivery_file_name", "")).strip() or f"{vault_id}-delivery.zip"
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
        updated_vault, outcome = cosmos_service.add_activation_request(
            vault_id=vault_id,
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

    return _build_recipient_vault_summary(updated_vault, recipient_email)


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
        existing_vault = cosmos_service.get_vault_by_id(vault_id)
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

    try:
        updated_vault = cosmos_service.remove_activation_request(
            vault_id=vault_id,
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

    return _build_recipient_vault_summary(updated_vault, recipient_email)


@app.get("/vaults/{vault_id}/recipients")
def list_vault_recipients(
    vault_id: str,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    cosmos_service = get_cosmos_service(request)
    vault_item = get_owned_vault_or_404(
        cosmos_service=cosmos_service,
        vault_id=vault_id,
        user_id=str(current_user.get("id", "")),
    )

    recipients = vault_item.get("recipients", [])
    if not isinstance(recipients, list):
        recipients = []

    return {
        "vault_id": vault_id,
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
    recipient_email = payload.email.strip().lower()
    get_owned_vault_or_404(
        cosmos_service=cosmos_service,
        vault_id=vault_id,
        user_id=str(current_user.get("id", "")),
    )

    if not is_valid_email(recipient_email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid recipient email is required.",
        )

    try:
        updated_vault = cosmos_service.add_recipient_to_vault(vault_id, recipient_email)
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

    return {
        "vault_id": vault_id,
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
    get_owned_vault_or_404(
        cosmos_service=cosmos_service,
        vault_id=vault_id,
        user_id=str(current_user.get("id", "")),
    )

    if not is_valid_email(normalized_email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid recipient email is required.",
        )

    try:
        updated_vault = cosmos_service.remove_recipient_from_vault(vault_id, normalized_email)
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
        "vault_id": vault_id,
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
        vault_id=vault_id,
        user_id=str(current_user.get("id", "")),
    )
    files = vault_item.get("files", [])
    if not isinstance(files, list):
        files = []

    return {
        "vault_id": vault_id,
        "files": files,
    }


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
        vault_id=vault_id,
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
        vault_id=vault_id,
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
        blob_service.delete_blob(container_name=container_name, blob_name=blob_name)
        updated_vault = cosmos_service.remove_file_from_vault(vault_id, file_id)
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

    vault_item = get_owned_vault_or_404(
        cosmos_service=cosmos_service,
        vault_id=vault_id,
        user_id=str(current_user.get("id", "")),
    )

    uploaded_file_metadata = None
    try:
        vault_item = ensure_vault_key_metadata(
            cosmos_service=cosmos_service,
            vault_key_service=vault_key_service,
            vault_item=vault_item,
        )

        plaintext_bytes = await file.read()
        encryption_payload = encrypt_file_bytes(
            plaintext_bytes,
            vault_item.get("public_jwk", {}),
        )

        uploaded_blob_metadata = blob_service.upload_bytes(
            vault_id=vault_id,
            payload=encryption_payload["ciphertext"],
            file_name=file.filename,
            content_type=file.content_type,
            blob_content_type="application/octet-stream",
        )
        uploaded_file_metadata = dict(uploaded_blob_metadata)
        uploaded_file_metadata.update(encryption_payload["metadata"])
        uploaded_file_metadata["content_type"] = file.content_type
        uploaded_file_metadata["blob_content_type"] = "application/octet-stream"
        uploaded_file_metadata["size_bytes"] = len(plaintext_bytes)
        uploaded_file_metadata["ciphertext_size_bytes"] = len(encryption_payload["ciphertext"])
        uploaded_file_metadata["key_kid"] = str(vault_item.get("key_kid", "")).strip()
        uploaded_file_metadata["key_version"] = str(vault_item.get("key_version", "")).strip()

        existing_files = vault_item.get("files", [])
        if not isinstance(existing_files, list):
            existing_files = []
        updated_files = existing_files + [uploaded_file_metadata]

        updated_vault = cosmos_service.update_vault(vault_id, {"files": updated_files})
        if updated_vault is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vault not found during metadata update.",
            )

        return {
            "vault_id": vault_id,
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
