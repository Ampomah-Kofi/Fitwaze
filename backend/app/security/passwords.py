"""Password hashing using Argon2id (argon2-cffi).

Argon2id is the OWASP-recommended default for password hashing. We use the
library's default parameters (time_cost, memory_cost, parallelism), which are
tuned for Argon2id and reasonable for an interactive login endpoint.
"""
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

_hasher = PasswordHasher()  # defaults to Argon2id


def hash_password(plain_password: str) -> str:
    """Hash a plaintext password with Argon2id. Returns an encoded hash string
    (e.g. "$argon2id$v=19$m=65536,t=3,p=4$...") suitable for storage."""
    return _hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Verify a plaintext password against a stored Argon2id hash.

    Returns False on any mismatch or malformed-hash error rather than raising,
    so callers can treat verification failure uniformly (e.g. to increment a
    failed-login counter) without needing to catch library-specific errors.
    """
    try:
        return _hasher.verify(password_hash, plain_password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Whether a stored hash should be re-hashed (e.g. hasher params changed)."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True
