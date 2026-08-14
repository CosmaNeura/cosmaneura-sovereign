"""
Lab-results client — the Sovereign's façade over the vendored athenahealth
FHIR adapter (`app/clients/athena/`).

Why this layer exists rather than calling the vendored tools directly:

  1. **The event loop.** Everything in this service is async httpx; the
     vendored client is synchronous `requests`. Calling it inline would block
     the single web process for the duration of every EMR round-trip. Each
     call is therefore dispatched with `asyncio.to_thread`.
  2. **Feature flag.** `FEATURE_ATHENA_LABS` gates the whole capability, so a
     production build can exclude the sandbox feature entirely.
  3. **Authorization.** The Sovereign has no patient roster and cannot know
     which patients a clinician may see — only the host app does. So the
     patient must arrive as host-supplied context (`metadata.patient_ref`),
     and free-text patient *search* is refused outside the sandbox unless
     explicitly enabled. Without this, a model could name any patient in the
     practice and the tool would happily fetch their labs.
  4. **PHI-safe errors.** Failures never carry EMR payloads outward.

Compliance posture: lab values are PHI. This client is provider-persona only,
runs behind the BAA tripwire, and its output must never be forwarded to a
non-BAA hop (Magisterium, PubMed, ClinicalTrials.gov) except through the
orchestrator's de-identified copy.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Optional

from app.clients.athena import (AthenaFhirLabSource, FHIR_READ_SCOPES,
                                LabReport, Settings as AthenaSettings,
                                TokenProvider)
from app.compliance.netsec import assert_safe_outbound_url
from app.config import Settings, get_settings


class LabsUnavailable(RuntimeError):
    """The lab capability is off or not configured."""


class LabsNotAuthorized(RuntimeError):
    """The request would reach a patient the host has not authorized."""


# Deterministic stubs so the whole lane runs with zero credentials, matching
# the shape (and the documented sandbox quirks) of real athenahealth data:
# some analytes carry no LOINC, and report status is often "unknown".
_MOCK_REPORTS = [
    LabReport(
        report_id="mock-r1", name="Comprehensive metabolic panel",
        status="final", collected_at="2026-01-14T00:00:00Z",
        values=[],
    ),
]


def _mock_payload() -> list[LabReport]:
    from app.clients.athena import LabValue
    r1 = LabReport(report_id="mock-r1", name="Hemoglobin A1c", status="final",
                   collected_at="2025-08-14T00:00:00Z")
    r1.values = [LabValue(name="Hemoglobin A1C", loinc="4548-4", value="7.6",
                          unit="%", reference_range="4.0-5.6",
                          abnormal_flag="high", observed_at="2025-08-14T00:00:00Z",
                          source_id="mock-o1")]
    r2 = LabReport(report_id="mock-r2", name="Comprehensive metabolic panel",
                   status="unknown", collected_at="2026-02-02T00:00:00Z")
    r2.values = [
        LabValue(name="Hemoglobin A1C", loinc="4548-4", value="8.2", unit="%",
                 reference_range="4.0-5.6", abnormal_flag="high",
                 observed_at="2026-02-02T00:00:00Z", source_id="mock-o2"),
        LabValue(name="Ferritin", loinc=None, value="18", unit="ng/mL",
                 reference_range="20-200", abnormal_flag="low",
                 observed_at="2026-02-02T00:00:00Z", source_id="mock-o3"),
        LabValue(name="Sodium", loinc="2951-2", value="140", unit="mmol/L",
                 reference_range="136-145", abnormal_flag="normal",
                 observed_at="2026-02-02T00:00:00Z", source_id="mock-o4"),
    ]
    r3 = LabReport(report_id="mock-r3", name="Voided panel",
                   status="entered-in-error", collected_at="2026-02-03T00:00:00Z")
    return [r1, r2, r3]


class LabsClient:
    def __init__(self, settings: Optional[Settings] = None):
        self.s = settings or get_settings()
        self._source_cache: Any = None

    # ------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self.s.feature_athena_labs)

    @property
    def sandbox(self) -> bool:
        return self.s.athena_env != "production"

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise LabsUnavailable(
                "The lab-results capability is not enabled on this deployment."
            )

    def _source(self):
        if self._source_cache is None:
            if not (self.s.athena_client_id and self.s.athena_client_secret):
                raise LabsUnavailable(
                    "Lab results are enabled but athenahealth credentials are "
                    "not configured."
                )
            cfg = AthenaSettings(
                client_id=self.s.athena_client_id,
                client_secret=self.s.athena_client_secret,
                env=self.s.athena_env,
                practice_id=self.s.athena_practice_id,
                department_id=self.s.athena_department_id,
            )
            # Same outbound posture as every other client here.
            assert_safe_outbound_url(cfg.token_url)
            assert_safe_outbound_url(cfg.fhir_base)
            self._source_cache = AthenaFhirLabSource(
                cfg, TokenProvider(cfg, FHIR_READ_SCOPES))
        return self._source_cache

    async def _reports(self, patient_id: str, since: Optional[str]) -> list[LabReport]:
        if self.s.mock_mode:
            reports = _mock_payload()
        else:
            # Sync `requests` client -> keep it off the event loop.
            reports = await asyncio.to_thread(
                self._source().get_lab_reports, patient_id, since)
        # Documented quirk: voided reports exist and must never be shown.
        return [r for r in reports if r.status != "entered-in-error"]

    # ------------------------------------------------------------------
    # Tools (same four capabilities as the bundle, Sovereign-shaped)
    # ------------------------------------------------------------------
    async def find_patient(self, family: str, given: Optional[str] = None,
                           birthdate: Optional[str] = None) -> dict:
        """
        Search by name. REFUSED outside the sandbox unless explicitly enabled:
        in production this would let a model reach any patient in the practice,
        bypassing the host's own authorization.
        """
        self._require_enabled()
        if not self.sandbox and not self.s.labs_allow_patient_search:
            raise LabsNotAuthorized(
                "Patient search is disabled outside the sandbox. Open the "
                "patient's record so the host supplies the patient reference."
            )
        if self.s.mock_mode:
            matches = [{"id": "a-195900.E-60178", "name": "Donna Sandboxtest",
                        "birthdate": "1984-02-01"}]
        else:
            matches = await asyncio.to_thread(
                self._source().find_patient, family, given, birthdate)
        return {"matches": matches, "count": len(matches)}

    async def lab_results(self, patient_id: str, since: Optional[str] = None,
                          only_abnormal: bool = False) -> dict:
        self._require_enabled()
        out = []
        for rep in await self._reports(patient_id, since):
            values = [_value_dict(v) for v in rep.values]
            if only_abnormal:
                values = [v for v in values if _is_abnormal(v["abnormal_flag"])]
                if not values:
                    continue
            out.append({**_report_dict(rep), "values": values})
        return {"patient_id": patient_id, "report_count": len(out), "reports": out}

    async def analyte_trend(self, patient_id: str, analyte: str,
                            since: Optional[str] = None) -> dict:
        """
        Time series for one analyte, oldest first. Matches on LOINC when the
        source provides one and falls back to a name substring — sandbox data
        frequently has no LOINC at all.
        """
        self._require_enabled()
        needle = analyte.strip().lower()
        points = []
        for rep in await self._reports(patient_id, since):
            for v in rep.values:
                if (v.loinc and v.loinc == analyte) or (needle in (v.name or "").lower()):
                    points.append({
                        "observed_at": v.observed_at or rep.collected_at,
                        "value": v.value, "unit": v.unit,
                        "reference_range": v.reference_range,
                        "abnormal_flag": v.abnormal_flag,
                        "analyte_name": v.name, "loinc": v.loinc,
                        "report_id": rep.report_id,
                    })
        points.sort(key=lambda p: p["observed_at"] or "")
        return {"patient_id": patient_id, "analyte_query": analyte,
                "point_count": len(points), "points": points}

    async def abnormal_summary(self, patient_id: str,
                               since: Optional[str] = None) -> dict:
        """Abnormal values grouped by analyte — the 'what needs attention' view."""
        self._require_enabled()
        grouped: dict[str, list[dict]] = defaultdict(list)
        for rep in await self._reports(patient_id, since):
            for v in rep.values:
                if _is_abnormal(v.abnormal_flag):
                    grouped[v.name or "?"].append({
                        "observed_at": v.observed_at or rep.collected_at,
                        "value": v.value, "unit": v.unit,
                        "reference_range": v.reference_range,
                        "flag": v.abnormal_flag,
                    })
        summary = []
        for name, occ in grouped.items():
            occ.sort(key=lambda p: p["observed_at"] or "")
            summary.append({"analyte": name, "occurrences": len(occ),
                            "latest": occ[-1], "history": occ})
        summary.sort(key=lambda s: s["latest"]["observed_at"] or "", reverse=True)
        return {"patient_id": patient_id, "abnormal_analytes": len(summary),
                "summary": summary}


def _is_abnormal(flag: Optional[str]) -> bool:
    return bool(flag) and flag.lower() not in ("normal", "n")


def _report_dict(rep: LabReport) -> dict:
    return {"report_id": rep.report_id, "name": rep.name, "status": rep.status,
            "collected_at": rep.collected_at, "resulted_at": rep.resulted_at,
            "performing_lab": rep.performing_lab, "has_document": rep.has_document}


def _value_dict(v) -> dict:
    return {"name": v.name, "loinc": v.loinc, "value": v.value, "unit": v.unit,
            "reference_range": v.reference_range,
            "abnormal_flag": v.abnormal_flag, "observed_at": v.observed_at}
