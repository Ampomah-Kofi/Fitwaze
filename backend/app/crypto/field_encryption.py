"""Field-level encryption at rest using AES-256-GCM.

Applied to the most sensitive `health_profiles` columns (height_cm,
weight_kg, diabetes_status, mobility_limitations) so that a raw database
dump/breach does not expose plaintext health data. The key comes from the
`FIELD_ENCRYPTION_KEY` environment variable (base64-encoded, 32 raw bytes),
loaded via `app/config.py` — never hardcoded.

Generate a key with:
    python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"

Ciphertext layout stored in the DB: base64( nonce (12 bytes) || ciphertext-and-tag ).
AES-GCM's authentication tag is appended to the ciphertext by the
`cryptography` library's `AESGCM.encrypt`, so nonce+ciphertext is everything
needed to decrypt and verify integrity.
"""
from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_SIZE_BYTES = 12
KEY_SIZE_BYTES = 32


class FieldEncryptionError(Exception):
    """Raised on key-format problems or decryption/authentication failure."""


def load_key(base64_key: str) -> bytes:
    """Decode+validate a base64-encoded 32-byte AES-256 key from settings."""
    if not base64_key:
        raise FieldEncryptionError(
            "FIELD_ENCRYPTION_KEY is not set. Generate one with: "
            "python -c \"import os,base64;print(base64.b64encode(os.urandom(32)).decode())\""
        )
    try:
        key = base64.b64decode(base64_key, validate=True)
    except Exception:  # noqa: BLE001
        # Hosting platforms may generate the key in the URL-safe base64
        # alphabet ("-" and "_" instead of "+" and "/"); accept that too.
        try:
            key = base64.urlsafe_b64decode(base64_key.encode("ascii"))
            if base64.urlsafe_b64encode(key).decode("ascii").rstrip("=") != base64_key.rstrip("="):
                raise ValueError("not canonical")
        except Exception as exc:  # noqa: BLE001
            raise FieldEncryptionError("FIELD_ENCRYPTION_KEY is not valid base64") from exc
    if len(key) != KEY_SIZE_BYTES:
        raise FieldEncryptionError(
            f"FIELD_ENCRYPTION_KEY must decode to {KEY_SIZE_BYTES} bytes, got {len(key)}"
        )
    return key


def encrypt_field(plaintext: str, key: bytes) -> bytes:
    """Encrypt a plaintext string with AES-256-GCM.

    Returns raw bytes: nonce (12 bytes) || ciphertext+tag. Store this
    directly in a BYTEA/LargeBinary column, or base64-encode it for a text
    column (see `encrypt_field_b64`/`decrypt_field_b64` below).
    """
    aesgcm = AESGCM(key)
    nonce = os.urandom(NONCE_SIZE_BYTES)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
    return nonce + ciphertext


def decrypt_field(ciphertext: bytes, key: bytes) -> str:
    """Decrypt bytes produced by `encrypt_field`. Raises FieldEncryptionError
    if the ciphertext is malformed or authentication fails (tampering)."""
    if len(ciphertext) < NONCE_SIZE_BYTES:
        raise FieldEncryptionError("ciphertext too short to contain a nonce")
    nonce, actual_ciphertext = ciphertext[:NONCE_SIZE_BYTES], ciphertext[NONCE_SIZE_BYTES:]
    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(nonce, actual_ciphertext, associated_data=None)
    except InvalidTag as exc:
        raise FieldEncryptionError("failed to decrypt field (invalid key or tampered data)") from exc
    return plaintext.decode("utf-8")


def encrypt_field_b64(plaintext: str, key: bytes) -> str:
    """Convenience wrapper storing the encrypted blob as a base64 text string
    (used for the `health_profiles` encrypted columns, defined as Text)."""
    return base64.b64encode(encrypt_field(plaintext, key)).decode("ascii")


def decrypt_field_b64(encoded_ciphertext: str, key: bytes) -> str:
    return decrypt_field(base64.b64decode(encoded_ciphertext), key)
