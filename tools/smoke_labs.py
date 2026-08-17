"""Bounded athenahealth Preview smoke for governed ``labs.read``.

Run with ``PYTHONPATH=. .venv/bin/python tools/smoke_labs.py``. The script
requires only ``ATHENA_CLIENT_ID`` and ``ATHENA_CLIENT_SECRET`` and is pinned
to the known synthetic Preview practice/record. It never searches by name,
never calls an LLM, performs one logical read, and derives summaries locally.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta

from app.clients.labs import (
    SANDBOX_LABEL,
    LabsCategory,
    LabsClient,
    LabsPurpose,
    LabsReadAccess,
    LabsReadRequest,
    LabsTier,
    LabsTimeWindow,
    abnormal_summary,
    analyte_trend,
)
from app.compliance.audit import AuditLogger
from app.config import ComplianceProfile, MountProfile, Persona, Settings
from app.security.mount import MountContext

PREVIEW_PRACTICE = "195900"
SYNTHETIC_RECORD_REF = "a-195900.E-60178"


def _hint(err: str) -> str:
    lowered = err.lower()
    if "401" in lowered or "invalid_client" in lowered or "token request failed" in lowered:
        return "Hint: re-copy the Preview client id and secret from the Developer Console."
    if "403" in lowered:
        return (
            "Hint: the Preview app needs only DiagnosticReport.read and "
            "Observation.read FHIR scopes."
        )
    if "429" in lowered:
        return "Hint: the Preview rate limit was reached; wait briefly and retry."
    if any(
        token in lowered
        for token in ("connection", "timed out", "timeout", "resolve", "ssl")
    ):
        return "Hint: check network access to api.preview.platform.athenahealth.com."
    return "Hint: share this synthetic-data-only failure with the developer."


def _fail(stage: str, exc: Exception) -> None:
    print(f"[FAIL] {stage}: {exc}")
    print("       " + _hint(str(exc)))


def _access(settings: Settings, request: LabsReadRequest) -> LabsReadAccess:
    profile = ComplianceProfile(Persona.PROVIDER)
    mount = MountContext(
        surface="provider_assistant",
        auth_tier="provider",
        capabilities=("ehr:labs:read",),
        tenant_id=request.tenant_ref,
        actor_id=request.actor_ref,
    )
    return LabsReadAccess(
        trace_id="labs_preview_smoke_trace",
        expected_turn_request_ref=request.turn_request_ref,
        authorized_record_ref=request.record_ref,
        expected_purpose=request.purpose,
        expected_categories=request.categories,
        expected_time_window=request.time_window,
        expected_tier=request.tier,
        mount=mount,
        profile=profile,
        audit=AuditLogger(settings),
        record_scope_source="host_open_record",
    )


async def main() -> int:
    settings = Settings(
        mock_mode=False,
        feature_athena_labs=True,
        mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_preview195900",
        athena_env="preview",
        athena_practice_id=PREVIEW_PRACTICE,
        labs_allow_patient_search=False,
        audit_sink="stdout",
    )
    print(f"{SANDBOX_LABEL}; practice={settings.athena_practice_id}")

    if not (settings.athena_client_id and settings.athena_client_secret):
        print(
            "[FAIL] Credentials: ATHENA_CLIENT_ID and ATHENA_CLIENT_SECRET "
            "must both be set."
        )
        return 1
    print("[PASS] Preview credentials present")

    labs = LabsClient(settings)
    try:
        await asyncio.to_thread(labs._source().tokens.get_token)
        print("[PASS] OAuth token issued with minimal labs-only scopes")
    except Exception as exc:  # every failure kind gets a synthetic-safe hint
        _fail("OAuth token", exc)
        return 1

    request = LabsReadRequest(
        turn_request_ref="labs_preview_smoke_turn",
        actor_ref="actor_preview_smoke",
        tenant_ref="tenant_preview195900",
        record_ref=SYNTHETIC_RECORD_REF,
        purpose=LabsPurpose.LAB_REVIEW,
        categories=(LabsCategory.LABORATORY,),
        analytes=(),
        time_window=LabsTimeWindow(
            since=date.today() - timedelta(days=730), until=date.today()
        ),
        tier=LabsTier.SANDBOX,
    )
    try:
        response = await labs.read(request, _access(settings, request))
        if response.result_count == 0:
            print(
                "[FAIL] Bounded labs.read: the known synthetic record returned "
                "no discrete values in the 730-day window."
            )
            print(
                "       Hint: confirm the Preview fixture and record id before "
                "certifying the lane."
            )
            return 1
        print(
            f"[PASS] One bounded labs.read returned {response.result_count} "
            "discrete values (entered-in-error excluded)"
        )
    except Exception as exc:
        _fail("Bounded labs.read", exc)
        return 1

    summary = abnormal_summary(response)
    trend = analyte_trend(response, "a1c")
    print(
        f"[PASS] Local views derived without another EHR read: "
        f"{summary['abnormal_analytes']} abnormal analytes; "
        f"{trend['point_count']} A1C points"
    )
    print("All stages passed against synthetic athenahealth Preview data.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
