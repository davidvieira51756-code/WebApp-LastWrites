from __future__ import annotations

import logging
import os
import re
from typing import List, Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

try:
    from backend.models.vault import VaultCreate, VaultResponse, VaultUpdate
    from backend.services.blob_service import BlobService
    from backend.services.cosmos_service import CosmosService
    from backend.services.keyvault_service import KeyVaultService
    from backend.services.local_blob_service import LocalBlobService
    from backend.services.local_cosmos_service import LocalCosmosService
except ModuleNotFoundError:
    from models.vault import VaultCreate, VaultResponse, VaultUpdate
    from services.blob_service import BlobService
    from services.cosmos_service import CosmosService
    from services.keyvault_service import KeyVaultService
    from services.local_blob_service import LocalBlobService
    from services.local_cosmos_service import LocalCosmosService

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

EMAIL_ADDRESS_REGEX = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$"
)


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

            app.state.keyvault_service = keyvault_service
            logger.info("Application startup completed.")

        app.state.cosmos_service = cosmos_service
        app.state.blob_service = blob_service
    except Exception:
        logger.exception("Application startup failed during service initialization.")
        raise


@app.post("/vaults", response_model=VaultResponse, status_code=status.HTTP_201_CREATED)
def create_vault(vault: VaultCreate, request: Request) -> VaultResponse:
    cosmos_service = get_cosmos_service(request)

    try:
        created_item = cosmos_service.create_vault(vault.model_dump())
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
def list_vaults(request: Request, user_id: Optional[str] = None) -> List[VaultResponse]:
    cosmos_service = get_cosmos_service(request)

    try:
        vault_items = cosmos_service.list_vaults(user_id=user_id)
        return [VaultResponse(**vault_item) for vault_item in vault_items]
    except Exception:
        logger.exception("Unhandled error while listing vaults.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list vaults.",
        ) from None


@app.get("/vaults/{vault_id}", response_model=VaultResponse)
def get_vault(vault_id: str, request: Request) -> VaultResponse:
    cosmos_service = get_cosmos_service(request)

    try:
        vault_item = cosmos_service.get_vault_by_id(vault_id)
    except Exception:
        logger.exception("Unhandled error while retrieving vault id=%s", vault_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve vault.",
        ) from None

    if vault_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vault not found.",
        )

    return VaultResponse(**vault_item)


@app.patch("/vaults/{vault_id}", response_model=VaultResponse)
def update_vault(vault_id: str, payload: VaultUpdate, request: Request) -> VaultResponse:
    cosmos_service = get_cosmos_service(request)
    update_data = payload.model_dump(exclude_unset=True)

    try:
        if "recipients" in update_data and update_data["recipients"] is not None:
            update_data["recipients"] = normalize_recipients(update_data["recipients"])

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
def delete_vault(vault_id: str, request: Request) -> None:
    cosmos_service = get_cosmos_service(request)
    blob_service = get_blob_service(request)

    try:
        vault_item = cosmos_service.get_vault_by_id(vault_id)
    except Exception:
        logger.exception("Unhandled error while reading vault before delete id=%s", vault_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read vault before delete.",
        ) from None

    if vault_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vault not found.",
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


@app.get("/vaults/{vault_id}/recipients")
def list_vault_recipients(vault_id: str, request: Request):
    cosmos_service = get_cosmos_service(request)

    try:
        vault_item = cosmos_service.get_vault_by_id(vault_id)
    except Exception:
        logger.exception("Unhandled error while reading recipients. vault_id=%s", vault_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list recipients.",
        ) from None

    if vault_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vault not found.",
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
):
    cosmos_service = get_cosmos_service(request)
    recipient_email = payload.email.strip().lower()

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
def delete_vault_recipient(vault_id: str, recipient_email: str, request: Request):
    cosmos_service = get_cosmos_service(request)
    normalized_email = recipient_email.strip().lower()

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
def list_vault_files(vault_id: str, request: Request):
    cosmos_service = get_cosmos_service(request)

    try:
        files = cosmos_service.get_vault_files(vault_id)
    except Exception:
        logger.exception("Unhandled error while listing vault files. vault_id=%s", vault_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list vault files.",
        ) from None

    if files is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vault not found.",
        )

    return {
        "vault_id": vault_id,
        "files": files,
    }


@app.get("/vaults/{vault_id}/files/{file_id}/download")
def get_vault_file_download_url(vault_id: str, file_id: str, request: Request):
    cosmos_service = get_cosmos_service(request)
    blob_service = get_blob_service(request)

    try:
        vault_item = cosmos_service.get_vault_by_id(vault_id)
    except Exception:
        logger.exception("Unhandled error while reading vault for download. vault_id=%s", vault_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to prepare download URL.",
        ) from None

    if vault_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vault not found.",
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
        expires_in_minutes = int(os.getenv("FILE_DOWNLOAD_SAS_EXPIRY_MINUTES", "15"))
    except ValueError:
        logger.warning(
            "Invalid FILE_DOWNLOAD_SAS_EXPIRY_MINUTES value, defaulting to 15 minutes."
        )
        expires_in_minutes = 15

    try:
        sas_payload = blob_service.generate_read_sas_url(
            container_name=container_name,
            blob_name=blob_name,
            expires_in_minutes=expires_in_minutes,
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
            "Unhandled error while generating SAS URL. vault_id=%s file_id=%s",
            vault_id,
            file_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate download URL.",
        ) from None

    download_url = sas_payload["url"]
    if getattr(blob_service, "is_local", False) and download_url.startswith("/"):
        base_url = str(request.base_url).rstrip("/")
        download_url = f"{base_url}{download_url}"

    return {
        "vault_id": vault_id,
        "file_id": file_id,
        "file_name": file_metadata.get("file_name"),
        "download_url": download_url,
        "expires_at": sas_payload["expires_at"],
    }


@app.delete("/vaults/{vault_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vault_file(vault_id: str, file_id: str, request: Request) -> None:
    cosmos_service = get_cosmos_service(request)
    blob_service = get_blob_service(request)

    try:
        vault_item = cosmos_service.get_vault_by_id(vault_id)
    except Exception:
        logger.exception("Unhandled error while reading vault before file delete. vault_id=%s", vault_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read vault before deleting file.",
        ) from None

    if vault_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vault not found.",
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
):
    cosmos_service = get_cosmos_service(request)
    blob_service = get_blob_service(request)

    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File name is required.",
        )

    try:
        vault_item = cosmos_service.get_vault_by_id(vault_id)
    except Exception:
        logger.exception("Failed to read vault before file upload. id=%s", vault_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read vault.",
        ) from None

    if vault_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vault not found.",
        )

    file_size = None
    try:
        file.file.seek(0, os.SEEK_END)
        file_size = file.file.tell()
        file.file.seek(0)
    except Exception:
        logger.debug("Could not determine file size for uploaded file.")

    uploaded_file_metadata = None
    try:
        uploaded_file_metadata = blob_service.upload_file(
            vault_id=vault_id,
            file_stream=file.file,
            file_name=file.filename,
            content_type=file.content_type,
            file_size=file_size,
        )

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
