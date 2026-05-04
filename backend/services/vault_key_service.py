from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from azure.identity import DefaultAzureCredential
from azure.keyvault.keys import KeyClient
from azure.keyvault.keys.crypto import CryptographyClient, EncryptionAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

try:
    from backend.services.file_crypto_service import b64url_decode, public_jwk_from_rsa_public_key
except ModuleNotFoundError:
    from services.file_crypto_service import b64url_decode, public_jwk_from_rsa_public_key

logger = logging.getLogger(__name__)

RSA_KEY_TYPE = "RSA"
RSA_WRAP_ALGORITHM = "RSA-OAEP-256"
RSA_KEY_METADATA_VERSION = 2
DEFAULT_RSA_KEY_SIZE = 4096


def _env_to_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _build_key_name(vault_id: str) -> str:
    sanitized = "".join(character if character.isalnum() else "-" for character in vault_id.lower())
    sanitized = "-".join(part for part in sanitized.split("-") if part)
    if not sanitized:
        sanitized = "default"

    name = f"vault-key-{sanitized}"
    return name[:120].rstrip("-")


class AzureVaultKeyService:
    def __init__(self, key_vault_url: Optional[str] = None) -> None:
        self._key_vault_url = (key_vault_url or os.getenv("KEY_VAULT_URL", "")).strip()
        if not self._key_vault_url:
            raise ValueError("Environment variable KEY_VAULT_URL is required for AzureVaultKeyService.")

        self._credential = DefaultAzureCredential()
        self._key_client = KeyClient(
            vault_url=self._key_vault_url,
            credential=self._credential,
        )
        self._key_size = int(os.getenv("KEY_VAULT_RSA_KEY_SIZE", str(DEFAULT_RSA_KEY_SIZE)))
        self._hardware_protected = _env_to_bool(os.getenv("KEY_VAULT_RSA_HARDWARE_PROTECTED", "false"))

    @staticmethod
    def _public_jwk_from_key(key: Any) -> Dict[str, Any]:
        key_jwk = key.key
        key_dict = key_jwk.to_dict() if hasattr(key_jwk, "to_dict") else {}
        modulus = key_dict.get("n", getattr(key_jwk, "n", None))
        exponent = key_dict.get("e", getattr(key_jwk, "e", None))

        if modulus is None or exponent is None:
            raise ValueError("Key Vault key does not expose a usable RSA public JWK.")

        public_numbers = rsa.RSAPublicNumbers(
            exponent if isinstance(exponent, int) else int.from_bytes(bytes(exponent), byteorder="big"),
            modulus if isinstance(modulus, int) else int.from_bytes(bytes(modulus), byteorder="big"),
        )
        public_key = public_numbers.public_key()
        return {
            "public_jwk": public_jwk_from_rsa_public_key(public_key),
            "key_size_bits": public_key.key_size,
        }

    def ensure_vault_key(self, vault_id: str) -> Dict[str, Any]:
        key_name = _build_key_name(vault_id)

        try:
            key = self._key_client.get_key(key_name)
            current_metadata = self._public_jwk_from_key(key)
            if int(current_metadata["key_size_bits"]) < self._key_size:
                logger.info(
                    "Vault RSA key is below requested size. Rotating key=%s old_bits=%s new_bits=%s",
                    key_name,
                    current_metadata["key_size_bits"],
                    self._key_size,
                )
                key = self._key_client.create_rsa_key(
                    name=key_name,
                    size=self._key_size,
                    hardware_protected=self._hardware_protected,
                    exportable=False,
                    key_operations=["encrypt", "decrypt", "wrapKey", "unwrapKey"],
                    tags={"vault_id": vault_id},
                )
        except Exception:
            logger.info("Vault RSA key not found for vault_id=%s. Creating key=%s", vault_id, key_name)
            key = self._key_client.create_rsa_key(
                name=key_name,
                size=self._key_size,
                hardware_protected=self._hardware_protected,
                exportable=False,
                key_operations=["encrypt", "decrypt", "wrapKey", "unwrapKey"],
                tags={"vault_id": vault_id},
            )

        public_key_metadata = self._public_jwk_from_key(key)
        return {
            "key_name": key_name,
            "key_kid": str(key.id),
            "key_version": str(key.properties.version or ""),
            "key_type": RSA_KEY_TYPE,
            "key_algorithm": RSA_WRAP_ALGORITHM,
            "key_size_bits": int(public_key_metadata["key_size_bits"]),
            "key_schema_version": RSA_KEY_METADATA_VERSION,
            "public_jwk": public_key_metadata["public_jwk"],
        }

    def unwrap_file_key(self, *, key_kid: str, wrapped_key: str) -> bytes:
        crypto_client = CryptographyClient(key_kid, credential=self._credential)
        decrypted = crypto_client.decrypt(
            EncryptionAlgorithm.rsa_oaep_256,
            b64url_decode(wrapped_key),
        )
        return bytes(decrypted.plaintext)


class LocalVaultKeyService:
    def __init__(self, keys_dir: Optional[str] = None) -> None:
        default_dir = Path(__file__).resolve().parents[1] / ".local_data" / "vault_keys"
        self._keys_dir = Path(keys_dir) if keys_dir else Path(
            os.getenv("LOCAL_VAULT_KEYS_DIR", str(default_dir))
        )

    def _private_key_path(self, key_name: str) -> Path:
        return self._keys_dir / f"{key_name}.pem"

    def _private_key_version_path(self, key_name: str, version: str) -> Path:
        return self._keys_dir / f"{key_name}--{version}.pem"

    def _metadata_path(self, key_name: str) -> Path:
        return self._keys_dir / f"{key_name}.json"

    def _read_active_version(self, key_name: str) -> Optional[str]:
        metadata_path = self._metadata_path(key_name)
        if not metadata_path.exists():
            return None
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        version = str(payload.get("active_version", "")).strip()
        return version or None

    def _write_active_version(self, key_name: str, version: str) -> None:
        self._metadata_path(key_name).write_text(
            json.dumps({"active_version": version}, indent=2),
            encoding="utf-8",
        )

    def _load_private_key(self, path: Path):
        return serialization.load_pem_private_key(path.read_bytes(), password=None)

    def _build_key_metadata(self, *, key_name: str, version: str, private_key) -> Dict[str, Any]:
        public_key = private_key.public_key()
        return {
            "key_name": key_name,
            "key_kid": f"local://vault-keys/{key_name}/versions/{version}",
            "key_version": version,
            "key_type": RSA_KEY_TYPE,
            "key_algorithm": RSA_WRAP_ALGORITHM,
            "key_size_bits": public_key.key_size,
            "key_schema_version": RSA_KEY_METADATA_VERSION,
            "public_jwk": public_jwk_from_rsa_public_key(public_key),
        }

    def ensure_vault_key(self, vault_id: str) -> Dict[str, Any]:
        self._keys_dir.mkdir(parents=True, exist_ok=True)
        key_name = _build_key_name(vault_id)
        active_version = self._read_active_version(key_name)
        if active_version:
            active_path = self._private_key_version_path(key_name, active_version)
            if active_path.exists():
                private_key = self._load_private_key(active_path)
                if private_key.key_size >= DEFAULT_RSA_KEY_SIZE:
                    return self._build_key_metadata(
                        key_name=key_name,
                        version=active_version,
                        private_key=private_key,
                    )

        legacy_path = self._private_key_path(key_name)
        if legacy_path.exists():
            legacy_private_key = self._load_private_key(legacy_path)
            if legacy_private_key.key_size >= DEFAULT_RSA_KEY_SIZE:
                self._write_active_version(key_name, "local")
                return self._build_key_metadata(
                    key_name=key_name,
                    version="local",
                    private_key=legacy_private_key,
                )

        version = f"v{uuid4().hex[:12]}"
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=DEFAULT_RSA_KEY_SIZE)
        self._private_key_version_path(key_name, version).write_bytes(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        self._write_active_version(key_name, version)
        return self._build_key_metadata(key_name=key_name, version=version, private_key=private_key)

    def unwrap_file_key(self, *, key_kid: str, wrapped_key: str) -> bytes:
        if key_kid.startswith("local://"):
            key_parts = key_kid.split("/")
            key_name = key_parts[3]
            key_version = key_parts[5] if len(key_parts) > 5 else "local"
        else:
            key_name = key_kid.rsplit("/", 2)[-2]
            key_version = key_kid.rsplit("/", 1)[-1] or "local"

        private_key_path = (
            self._private_key_path(key_name)
            if key_version == "local"
            else self._private_key_version_path(key_name, key_version)
        )
        if not private_key_path.exists():
            raise FileNotFoundError(f"Local RSA key file was not found for key_name={key_name}.")

        private_key = self._load_private_key(private_key_path)
        return private_key.decrypt(
            b64url_decode(wrapped_key),
            asym_padding.OAEP(
                mgf=asym_padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
