"""Closed-schema verification for provider open-record grants."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet

from app.security.record_grant import (
    RECORD_GRANT_TTL_SECONDS,
    RecordGrantConfigurationError,
    RecordGrantRejected,
    configured_record_grant_key,
    verify_record_grant,
)


NOW = datetime(2026, 8, 17, 12, 0, 30, tzinfo=UTC)
ISSUED = NOW - timedelta(seconds=30)
KEY = Fernet.generate_key()
ACTOR = "user_provider_0001"
VENDOR_RECORD = "a-195900.E-60178"


def _claims(**updates):
    claims = {
        "actor_ref": ACTOR,
        "adapter": "athena",
        "appointment_ref": "appointment_0001",
        "audience": "cosmaneura-sovereign",
        "doctor_ref": "doctor_0001",
        "environment": "preview",
        "expires_at": (
            ISSUED + timedelta(seconds=RECORD_GRANT_TTL_SECONDS)
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issued_at": ISSUED.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issuer": "cosmaneura-platform",
        "nonce": "12345678-1234-4678-9234-567812345678",
        "patient_ref": "patient_0001",
        "practice_ref": "195900",
        "synthetic_only": True,
        "tier": "sandbox",
        "vendor_record_ref": VENDOR_RECORD,
        "version": 1,
    }
    claims.update(updates)
    return claims


def _token(claims=None, *, plaintext: bytes | None = None) -> str:
    body = plaintext or json.dumps(
        claims or _claims(), separators=(",", ":"), sort_keys=True
    ).encode()
    return Fernet(KEY).encrypt_at_time(body, int(ISSUED.timestamp())).decode()


def test_verifies_exact_actor_bound_preview_grant():
    verified = verify_record_grant(
        _token(), key=KEY, expected_actor_ref=ACTOR, now=NOW
    )
    assert verified.vendor_record_ref == VENDOR_RECORD
    assert VENDOR_RECORD not in repr(verified)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("actor_ref", "user_other_0001"),
        ("adapter", "other"),
        ("audience", "other"),
        ("environment", "production"),
        ("issuer", "other"),
        ("practice_ref", "999999"),
        ("synthetic_only", False),
        ("tier", "production"),
        ("version", 2),
        ("version", True),
        ("nonce", "12345678-1234-1678-9234-567812345678"),
        ("vendor_record_ref", "Jane Smith"),
    ],
)
def test_rejects_wrong_actor_scope_and_claim_types(field, value):
    with pytest.raises(RecordGrantRejected, match="record grant rejected"):
        verify_record_grant(
            _token(_claims(**{field: value})),
            key=KEY,
            expected_actor_ref=ACTOR,
            now=NOW,
        )


def test_rejects_expired_tampered_extra_missing_and_duplicate_claims():
    expired_now = ISSUED + timedelta(seconds=RECORD_GRANT_TTL_SECONDS + 1)
    with pytest.raises(RecordGrantRejected):
        verify_record_grant(
            _token(), key=KEY, expected_actor_ref=ACTOR, now=expired_now
        )

    token = _token()
    replacement = "A" if token[-10] != "A" else "B"
    with pytest.raises(RecordGrantRejected):
        verify_record_grant(
            token[:-10] + replacement + token[-9:],
            key=KEY,
            expected_actor_ref=ACTOR,
            now=NOW,
        )

    with pytest.raises(RecordGrantRejected):
        verify_record_grant(
            _token(_claims(extra="authority")),
            key=KEY,
            expected_actor_ref=ACTOR,
            now=NOW,
        )
    missing = _claims()
    del missing["practice_ref"]
    with pytest.raises(RecordGrantRejected):
        verify_record_grant(
            _token(missing), key=KEY, expected_actor_ref=ACTOR, now=NOW
        )

    duplicate = json.dumps(_claims(), separators=(",", ":"))[:-1]
    duplicate += ',"actor_ref":"user_other_0001"}'
    with pytest.raises(RecordGrantRejected):
        verify_record_grant(
            _token(plaintext=duplicate.encode()),
            key=KEY,
            expected_actor_ref=ACTOR,
            now=NOW,
        )


def test_rejects_payload_lifetime_that_disagrees_with_sixty_second_contract():
    claims = _claims(
        expires_at=(ISSUED + timedelta(seconds=120)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    )
    with pytest.raises(RecordGrantRejected):
        verify_record_grant(
            _token(claims), key=KEY, expected_actor_ref=ACTOR, now=NOW
        )


def test_configuration_accepts_only_canonical_32_byte_fernet_key():
    assert configured_record_grant_key(KEY.decode()) == KEY
    for invalid in (
        None,
        "",
        "A" * 44,
        KEY.decode().rstrip("="),
        " " + KEY.decode(),
        KEY.decode()[:-2] + "==",
        KEY.decode()[:-2] + "/=",
    ):
        with pytest.raises(
            RecordGrantConfigurationError,
            match="SOVEREIGN_RECORD_GRANT_SECRET",
        ):
            configured_record_grant_key(invalid)
