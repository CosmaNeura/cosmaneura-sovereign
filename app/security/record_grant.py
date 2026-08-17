"""Verify short-lived provider record grants issued by the platform host.

The browser is transport only.  It never gets authority to choose an Athena
record: the platform derives the open synthetic record and encrypts a closed,
actor-bound grant.  Sovereign authenticates that grant before constructing a
``LabsReadRequest`` and releases only the vendor record reference to the
BAA-controlled Athena adapter.
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken


RECORD_GRANT_AUDIENCE = "cosmaneura-sovereign"
RECORD_GRANT_ISSUER = "cosmaneura-platform"
RECORD_GRANT_TTL_SECONDS = 60

_FERNET_KEY = re.compile(r"^[A-Za-z0-9_-]{43}=$")
_FERNET_TOKEN = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")
_OPAQUE_REF = re.compile(r"^[A-Za-z0-9._~-]+$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_CLAIM_KEYS = frozenset({
    "actor_ref",
    "adapter",
    "appointment_ref",
    "audience",
    "doctor_ref",
    "environment",
    "expires_at",
    "issued_at",
    "issuer",
    "nonce",
    "patient_ref",
    "practice_ref",
    "synthetic_only",
    "tier",
    "vendor_record_ref",
    "version",
})


class RecordGrantConfigurationError(RuntimeError):
    """The provider-only record-grant key is missing or malformed."""


class RecordGrantRejected(ValueError):
    """A supplied record grant is invalid, expired, or out of scope."""

    def __init__(self) -> None:
        super().__init__("record grant rejected")


@dataclass(frozen=True)
class VerifiedRecordGrant:
    """The minimum server-side result required by the Athena adapter."""

    vendor_record_ref: str = field(repr=False)


def configured_record_grant_key(raw: str | None) -> bytes:
    """Return one canonical Fernet key without ever reflecting its value."""

    value = raw if isinstance(raw, str) else ""
    if not _FERNET_KEY.fullmatch(value):
        raise RecordGrantConfigurationError(
            "SOVEREIGN_RECORD_GRANT_SECRET must be a canonical Fernet key"
        )
    try:
        decoded = base64.b64decode(
            value.encode("ascii"), altchars=b"-_", validate=True
        )
        canonical = base64.urlsafe_b64encode(decoded).decode("ascii")
    except (ValueError, UnicodeError):
        raise RecordGrantConfigurationError(
            "SOVEREIGN_RECORD_GRANT_SECRET must be a canonical Fernet key"
        ) from None
    if len(decoded) != 32 or canonical != value:
        raise RecordGrantConfigurationError(
            "SOVEREIGN_RECORD_GRANT_SECRET must be a canonical Fernet key"
        )
    return value.encode("ascii")


def _closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate claim")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> None:
    raise ValueError("invalid JSON constant")


def _opaque(value: Any, *, minimum: int = 3, maximum: int = 160) -> str:
    if (
        type(value) is not str
        or not minimum <= len(value) <= maximum
        or not _OPAQUE_REF.fullmatch(value)
    ):
        raise ValueError("invalid opaque reference")
    return value


def _timestamp(value: Any) -> datetime:
    if type(value) is not str or not _TIMESTAMP.fullmatch(value):
        raise ValueError("invalid timestamp")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def verify_record_grant(
    token: str,
    *,
    key: bytes,
    expected_actor_ref: str,
    now: datetime | None = None,
) -> VerifiedRecordGrant:
    """Authenticate a closed, actor-bound synthetic Athena Preview grant."""

    current = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    try:
        if (
            type(token) is not str
            or not 80 <= len(token) <= 8192
            or not _FERNET_TOKEN.fullmatch(token)
        ):
            raise ValueError("invalid token shape")
        expected_actor = _opaque(expected_actor_ref)
        plaintext = Fernet(key).decrypt_at_time(
            token.encode("ascii"),
            ttl=RECORD_GRANT_TTL_SECONDS,
            current_time=int(current.timestamp()),
        )
        if len(plaintext) > 4096:
            raise ValueError("oversized grant")
        claims = json.loads(
            plaintext.decode("utf-8"),
            object_pairs_hook=_closed_object,
            parse_constant=_invalid_constant,
        )
        if type(claims) is not dict or frozenset(claims) != _CLAIM_KEYS:
            raise ValueError("invalid claim set")
        if claims["actor_ref"] != expected_actor:
            raise ValueError("actor mismatch")
        for field in (
            "actor_ref",
            "appointment_ref",
            "doctor_ref",
            "patient_ref",
            "vendor_record_ref",
        ):
            _opaque(claims[field])
        if (
            claims["adapter"] != "athena"
            or claims["audience"] != RECORD_GRANT_AUDIENCE
            or claims["environment"] != "preview"
            or claims["issuer"] != RECORD_GRANT_ISSUER
            or claims["practice_ref"] != "195900"
            or claims["synthetic_only"] is not True
            or claims["tier"] != "sandbox"
            or type(claims["version"]) is not int
            or claims["version"] != 1
        ):
            raise ValueError("scope mismatch")
        nonce = UUID(claims["nonce"])
        if nonce.version != 4 or str(nonce) != claims["nonce"]:
            raise ValueError("invalid nonce")
        issued_at = _timestamp(claims["issued_at"])
        expires_at = _timestamp(claims["expires_at"])
        if expires_at - issued_at != timedelta(seconds=RECORD_GRANT_TTL_SECONDS):
            raise ValueError("invalid lifetime")
        if issued_at > current + timedelta(seconds=5) or current >= expires_at:
            raise ValueError("grant outside lifetime")
        return VerifiedRecordGrant(
            vendor_record_ref=claims["vendor_record_ref"],
        )
    except (InvalidToken, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise RecordGrantRejected() from None
