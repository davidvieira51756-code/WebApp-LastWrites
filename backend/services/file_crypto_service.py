from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Any, Dict

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(raw: str) -> bytes:
    padding_length = (-len(raw)) % 4
    return base64.urlsafe_b64decode(f"{raw}{'=' * padding_length}".encode("ascii"))


def sha256_hexdigest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _int_to_b64url(value: int) -> str:
    byte_length = max(1, (value.bit_length() + 7) // 8)
    return b64url_encode(value.to_bytes(byte_length, byteorder="big"))


def public_jwk_from_rsa_public_key(public_key: rsa.RSAPublicKey) -> Dict[str, str]:
    public_numbers = public_key.public_numbers()
    return {
        "kty": "RSA",
        "n": _int_to_b64url(public_numbers.n),
        "e": _int_to_b64url(public_numbers.e),
    }


def rsa_public_key_from_jwk(public_jwk: Dict[str, Any] | str) -> rsa.RSAPublicKey:
    if isinstance(public_jwk, str):
        parsed_jwk = json.loads(public_jwk)
    else:
        parsed_jwk = dict(public_jwk)

    modulus_raw = str(parsed_jwk.get("n", "")).strip()
    exponent_raw = str(parsed_jwk.get("e", "")).strip()
    if not modulus_raw or not exponent_raw:
        raise ValueError("public_jwk must include RSA modulus 'n' and exponent 'e'.")

    modulus = int.from_bytes(b64url_decode(modulus_raw), byteorder="big")
    exponent = int.from_bytes(b64url_decode(exponent_raw), byteorder="big")
    public_numbers = rsa.RSAPublicNumbers(exponent, modulus)
    return public_numbers.public_key()


def encrypt_file_bytes(plaintext: bytes, public_jwk: Dict[str, Any] | str) -> Dict[str, Any]:
    rsa_public_key = rsa_public_key_from_jwk(public_jwk)
    aes_key = secrets.token_bytes(32)
    iv = secrets.token_bytes(12)

    ciphertext_with_tag = AESGCM(aes_key).encrypt(iv, plaintext, None)
    ciphertext = ciphertext_with_tag[:-16]
    tag = ciphertext_with_tag[-16:]

    wrapped_key = rsa_public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    return {
        "ciphertext": ciphertext,
        "metadata": {
            "encrypted": True,
            "algorithm": "AES-256-GCM",
            "wrapped_key": b64url_encode(wrapped_key),
            "iv": b64url_encode(iv),
            "tag": b64url_encode(tag),
            "plaintext_sha256": sha256_hexdigest(plaintext),
            "ciphertext_sha256": sha256_hexdigest(ciphertext),
            "size_bytes": len(plaintext),
            "ciphertext_size_bytes": len(ciphertext),
        },
    }


def decrypt_file_bytes(
    ciphertext: bytes,
    *,
    aes_key: bytes,
    iv: str,
    tag: str,
) -> bytes:
    iv_bytes = b64url_decode(iv)
    tag_bytes = b64url_decode(tag)
    return AESGCM(aes_key).decrypt(iv_bytes, ciphertext + tag_bytes, None)
