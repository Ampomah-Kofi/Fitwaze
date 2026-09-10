"""Dialect-aware column types shared across models.

- `GUID`: stores UUID primary/foreign keys as native `UUID` on PostgreSQL and
  as a 36-char string on SQLite (used only by the test suite). UUID primary
  keys are used everywhere in this schema to avoid ID-enumeration attacks.
- `FlexGeometry`: stores PostGIS geometry columns natively on PostgreSQL
  (via GeoAlchemy2) and falls back to plain WKT text on SQLite for tests,
  since SQLite has no PostGIS/SpatiaLite support in this environment.
"""
from __future__ import annotations

import uuid

from geoalchemy2 import Geometry as GA2Geometry
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.types import CHAR, Text, TypeDecorator

from app.config import get_settings
from app.crypto.field_encryption import decrypt_field_b64, encrypt_field_b64, load_key


class GUID(TypeDecorator):
    """Platform-independent UUID type.

    Uses PostgreSQL's native UUID type when available, otherwise stores as a
    stringified CHAR(36) (SQLite, used in tests).
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return str(value)
        if not isinstance(value, uuid.UUID):
            return str(uuid.UUID(str(value)))
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


class FlexGeometry(TypeDecorator):
    """Geometry column: PostGIS `Geometry` on Postgres, WKT `Text` on SQLite.

    We deliberately do NOT override `process_bind_param`/`process_result_value`
    here — leaving them as identity lets SQLAlchemy delegate to whichever
    dialect-specific impl `load_dialect_impl` returns (GeoAlchemy2's Geometry
    bind/result processors on Postgres, plain string passthrough on SQLite).
    Callers should pass/expect WKT strings, e.g. "POINT(-74.006 40.7128)".
    """

    impl = Text
    cache_ok = True

    def __init__(self, geometry_type: str = "POINT", srid: int = 4326):
        super().__init__()
        self.geometry_type = geometry_type
        self.srid = srid

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(
                GA2Geometry(geometry_type=self.geometry_type, srid=self.srid)
            )
        return dialect.type_descriptor(Text())


class EncryptedString(TypeDecorator):
    """A Text column that transparently encrypts/decrypts its value with
    AES-256-GCM (see `app/crypto/field_encryption.py`) on the way in/out of
    the database. The key is read from `FIELD_ENCRYPTION_KEY` on every use
    (not cached at import time) so tests can set the env var per-process.

    Applied to `health_profiles.height_cm`, `weight_kg`, `diabetes_status`,
    and `mobility_limitations` — the most sensitive fields in the schema.
    Values are stored as strings; numeric fields are converted to/from
    string at the schema layer (see `app/schemas/profile.py`).
    """

    impl = Text
    cache_ok = False  # key can change between processes; do not cache plans

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        settings = get_settings()
        key = load_key(settings.field_encryption_key)
        return encrypt_field_b64(str(value), key)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        settings = get_settings()
        key = load_key(settings.field_encryption_key)
        return decrypt_field_b64(value, key)
