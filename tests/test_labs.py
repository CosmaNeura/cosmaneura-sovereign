"""
Lab-capability tests (offline, no credentials).

Two layers:
  1. The bundle's own tool assertions, re-pointed at the vendored client and
     the async façade — the delivered behaviour must survive integration.
  2. The gates this service adds: feature flag, provider persona, BAA
     attestation, and the patient-search authorization rule.
"""
import asyncio

from app.clients.athena import LabReport, LabValue
from app.clients.labs import (LabsClient, LabsNotAuthorized, LabsUnavailable)
from app.config import ComplianceProfile, Persona, Settings
from app.schemas import ChatRequest, Intent
from app.security.auth import Principal
from app.sovereign.orchestrator import Sovereign


def _run(coro):
    return asyncio.run(coro)


def _enabled(**kw):
    base = dict(
        feature_athena_labs=True,
        mount_surface="provider_assistant",
        mount_auth_tier="provider",
        mount_capabilities=[
            "education:answer",
            "values:answer",
            "referrals:search",
            "ethics:consult",
            "visit-prep:run",
            "plan-workbench:run",
            "ehr:labs:read",
            "journey-digest:write",
        ],
    )
    base.update(kw)
    return Settings(**base)


def _provider_principal():
    return Principal(
        subject="provider-labs-test-actor",
        persona=Persona.PROVIDER,
        roles=["provider"],
    )


class _FakeSource:
    """Stands in for the EMR, mirroring the bundle's fake source."""

    def find_patient(self, family, given=None, birthdate=None):
        return [{"id": "a-1.E-1", "name": "Donna Sandboxtest",
                 "birthdate": "1984-02-01"}]

    def get_lab_reports(self, patient_id, since=None):
        r1 = LabReport(report_id="r1", name="CMP", status="final",
                       collected_at="2026-01-01T00:00:00Z")
        r1.values = [
            LabValue(name="hemoglobin A1C", loinc="4548-4", value="6.8", unit="%",
                     reference_range="4.0-5.6", abnormal_flag="high",
                     observed_at="2026-01-01T00:00:00Z"),
            LabValue(name="sodium", loinc="2951-2", value="140", unit="mmol/L",
                     reference_range="136-145", abnormal_flag="normal",
                     observed_at="2026-01-01T00:00:00Z"),
        ]
        r2 = LabReport(report_id="r2", name="CMP", status="final",
                       collected_at="2026-06-01T00:00:00Z")
        r2.values = [
            LabValue(name="hemoglobin A1C", loinc="4548-4", value="7.2", unit="%",
                     reference_range="4.0-5.6", abnormal_flag="high",
                     observed_at="2026-06-01T00:00:00Z"),
        ]
        voided = LabReport(report_id="r3", name="Voided",
                           status="entered-in-error",
                           collected_at="2026-06-02T00:00:00Z")
        voided.values = [LabValue(name="hemoglobin A1C", value="99")]
        return [r1, r2, voided]


def _client_with_fake():
    c = LabsClient(_enabled(mock_mode=False, athena_client_id="x",
                            athena_client_secret="y"))
    c._source_cache = _FakeSource()
    return c


# --- delivered tool behaviour, preserved through integration -----------------

def test_find_patient_returns_matches():
    out = _run(_client_with_fake().find_patient("Sandboxtest"))
    assert out["count"] == 1
    assert out["matches"][0]["id"] == "a-1.E-1"


def test_entered_in_error_reports_are_filtered():
    out = _run(_client_with_fake().lab_results("a-1.E-1"))
    ids = [r["report_id"] for r in out["reports"]]
    assert "r3" not in ids and out["report_count"] == 2


def test_only_abnormal_filter():
    out = _run(_client_with_fake().lab_results("a-1.E-1", only_abnormal=True))
    names = {v["name"] for r in out["reports"] for v in r["values"]}
    assert names == {"hemoglobin A1C"}          # sodium was flagged normal


def test_trend_matches_by_loinc_and_by_name():
    c = _client_with_fake()
    by_code = _run(c.analyte_trend("a-1.E-1", "4548-4"))
    by_name = _run(c.analyte_trend("a-1.E-1", "a1c"))
    assert by_code["point_count"] == 2 and by_name["point_count"] == 2
    # Oldest first, and the voided 99 never appears.
    assert [p["value"] for p in by_code["points"]] == ["6.8", "7.2"]


def test_abnormal_summary_groups_by_analyte():
    out = _run(_client_with_fake().abnormal_summary("a-1.E-1"))
    assert out["abnormal_analytes"] == 1
    row = out["summary"][0]
    assert row["analyte"] == "hemoglobin A1C" and row["occurrences"] == 2
    assert row["latest"]["value"] == "7.2"      # latest, not first


# --- gates this service adds -------------------------------------------------

def test_capability_is_off_by_default():
    c = LabsClient(Settings())
    assert c.enabled is False
    try:
        _run(c.abnormal_summary("a-1.E-1"))
        assert False, "should refuse when the feature flag is off"
    except LabsUnavailable:
        pass


def test_patient_search_refused_outside_sandbox():
    """A model naming any patient in the practice must not reach their labs."""
    prod = LabsClient(_enabled(athena_env="production", mock_mode=False,
                               athena_client_id="x", athena_client_secret="y"))
    prod._source_cache = _FakeSource()
    try:
        _run(prod.find_patient("Sandboxtest"))
        assert False, "production name-search should be refused by default"
    except LabsNotAuthorized:
        pass
    # Sandbox is allowed; so is an explicit opt-in.
    assert _run(_client_with_fake().find_patient("Sandboxtest"))["count"] == 1
    opted = LabsClient(_enabled(athena_env="production", labs_allow_patient_search=True,
                                mock_mode=False, athena_client_id="x",
                                athena_client_secret="y"))
    opted._source_cache = _FakeSource()
    assert _run(opted.find_patient("Sandboxtest"))["count"] == 1


def test_enabled_without_credentials_fails_closed():
    c = LabsClient(_enabled(mock_mode=False))
    try:
        _run(c.abnormal_summary("a-1.E-1"))
        assert False, "should refuse without athenahealth credentials"
    except LabsUnavailable:
        pass


def test_lab_review_routes_and_returns_structured_payload():
    sov = Sovereign(_enabled())            # mock_mode default True
    resp = _run(sov.handle(ChatRequest(
        persona=Persona.PROVIDER,
        message="anything concerning in her lab results?",
        metadata={"patient_ref": "a-195900.E-60178"}),
        principal=_provider_principal()))
    assert resp.intent == Intent.LAB_REVIEW
    assert resp.structured.get("feature") == "lab_review"
    summary = resp.structured["abnormal_summary"]["summary"]
    analytes = {row["analyte"] for row in summary}
    assert "Hemoglobin A1C" in analytes and "Ferritin" in analytes
    assert "Sodium" not in analytes                     # normal value excluded
    # The audit payload carries a hash, never the patient identifier.
    access = resp.structured["access"]
    assert len(access["patient_ref_sha256_16"]) == 16
    assert "a-195900" not in str(access)


def test_lab_review_without_patient_asks_for_the_record():
    sov = Sovereign(_enabled(mock_mode=True))
    resp = _run(sov.handle(ChatRequest(
        persona=Persona.PROVIDER,
        message="what were the lab results out of range?"),
        principal=_provider_principal()))
    assert resp.intent == Intent.LAB_REVIEW
    assert resp.structured.get("status") == "no_patient"


def test_lab_review_requires_baa_attestation():
    from app.agents.labs_agent import LabReviewAgent
    agent = LabReviewAgent(_enabled(mock_mode=False, baa_attested=False))
    try:
        _run(agent.run(ChatRequest(persona=Persona.PROVIDER, message="labs?"),
                       ComplianceProfile(Persona.PROVIDER)))
        assert False, "PHI path must refuse without BAA attestation"
    except RuntimeError as e:
        assert "BAA" in str(e)


def test_disabled_feature_answers_gracefully_in_pipeline():
    sov = Sovereign(_enabled(feature_athena_labs=False))
    resp = _run(sov.handle(ChatRequest(
        persona=Persona.PROVIDER, message="anything abnormal in her labs?",
        metadata={"patient_ref": "a-1.E-1"}),
        principal=_provider_principal()))
    assert resp.structured.get("status") == "disabled"


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
    print("All lab tests passed.")
