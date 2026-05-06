from __future__ import annotations

import unittest

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from backend.services.file_crypto_service import (
    b64url_decode,
    decrypt_file_bytes,
    encrypt_file_bytes,
    public_jwk_from_rsa_public_key,
    rsa_key_size_bits_from_public_jwk,
    rsa_public_key_from_jwk,
    sha256_hexdigest,
)


class FileCryptoServiceTests(unittest.TestCase):
    def test_encrypt_file_bytes_round_trip_returns_expected_metadata(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_jwk = public_jwk_from_rsa_public_key(private_key.public_key())
        plaintext = b"encrypted test payload"

        payload = encrypt_file_bytes(plaintext, public_jwk)

        self.assertNotEqual(payload["ciphertext"], plaintext)
        self.assertTrue(payload["metadata"]["encrypted"])
        self.assertEqual(payload["metadata"]["algorithm"], "AES-256-GCM")
        self.assertEqual(payload["metadata"]["key_wrap_algorithm"], "RSA-OAEP-256")
        self.assertEqual(payload["metadata"]["plaintext_sha256"], sha256_hexdigest(plaintext))

        aes_key = private_key.decrypt(
            b64url_decode(payload["metadata"]["wrapped_key"]),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        decrypted = decrypt_file_bytes(
            payload["ciphertext"],
            aes_key=aes_key,
            iv=payload["metadata"]["iv"],
            tag=payload["metadata"]["tag"],
        )
        self.assertEqual(decrypted, plaintext)

    def test_public_jwk_round_trip_preserves_key_details(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = private_key.public_key()
        public_jwk = public_jwk_from_rsa_public_key(public_key)

        restored_public_key = rsa_public_key_from_jwk(public_jwk)

        self.assertEqual(restored_public_key.public_numbers(), public_key.public_numbers())
        self.assertEqual(rsa_key_size_bits_from_public_jwk(public_jwk), 2048)

    def test_rsa_public_key_from_jwk_requires_modulus_and_exponent(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "public_jwk must include RSA modulus 'n' and exponent 'e'.",
        ):
            rsa_public_key_from_jwk({"kty": "RSA"})


if __name__ == "__main__":
    unittest.main()
