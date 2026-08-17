"""Governed ``labs.read`` façade over the athenahealth Preview adapter.

The façade owns the versioned tool shape, operation-level authorization,
bounded read, two-phase audit, and response allowlist. Raw lab values leave
this module only in the typed response consumed by the BAA-covered lab agent.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.clients.athena import (
    AthenaFhirLabSource,
    FHIR_LABS_READ_SCOPES,
    LabReport,
    Settings as AthenaSettings,
    TokenProvider,
)
from app.compliance.audit import AuditLogger
from app.compliance.netsec import assert_safe_outbound_url
from app.config import ComplianceProfile, Persona, Settings, get_settings
from app.security.mount import MountContext


SANDBOX_LABEL = "Synthetic sandbox labs — no real patient data"
PRODUCTION_REQUIREMENTS_MESSAGE = (
    "Production lab access is unavailable: platform-resolved actor, tenant, "
    "and record scope; an executed BAA; athenaOne app enablement; and durable "
    "access-intent and outcome recording are required before any call."
)
_ADAPTER_NAME = "athenahealth_fhir_r4"
_OPAQUE_REF_RE = re.compile(r"^[A-Za-z0-9._~-]+$")
_ANALYTE_RE = re.compile(r"^[A-Za-z0-9 .%()+/_\[\]-]+$")
_INSTRUCTION_TEXT_RE = re.compile(
    r"\b(?:ignore|disregard|override|previous instructions?|system prompt|"
    r"developer message|assistant message|follow these instructions?)\b",
    re.IGNORECASE,
)
_UNIT_RE = re.compile(r"^[A-Za-z0-9%µμ./^*_-]+$")
_NUMERIC_VALUE_RE = re.compile(
    r"^(?:[<>]=?\s*)?[-+]?\d+(?:\.\d+)?(?:\s*[-–]\s*[-+]?\d+(?:\.\d+)?)?$"
)
_QUALITATIVE_VALUES = {
    "positive", "negative", "detected", "not detected", "reactive",
    "nonreactive", "normal", "abnormal", "present", "absent", "trace",
    "inconclusive", "indeterminate", "pending",
}
_FLAGS = {"normal", "n", "high", "h", "low", "l", "critical", "abnormal"}
_REPORT_STATUSES = {
    "final", "preliminary", "amended", "corrected", "registered", "partial",
    "unknown",
}
_MAX_WINDOW_DAYS = 730
_MAX_RESULTS = 500


class LabsUnavailable(RuntimeError):
    """The governed lab operation is disabled, misconfigured, or unreachable."""


class LabsNotAuthorized(RuntimeError):
    """The operation lacks a verified provider/record scope."""


class LabsProductionUnavailable(LabsNotAuthorized):
    """Production access is deliberately unavailable until every gate exists."""


class LabsPurpose(str, Enum):
    LAB_REVIEW = "lab_review"
    VISIT_PREP = "visit_prep"
    PLAN_WORKBENCH = "plan_workbench"


class LabsCategory(str, Enum):
    LABORATORY = "laboratory"


class LabsTier(str, Enum):
    SANDBOX = "sandbox"
    PRODUCTION = "production"


class LabsTimeWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    since: date
    until: date

    @model_validator(mode="after")
    def _ordered(self) -> "LabsTimeWindow":
        if self.since > self.until:
            raise ValueError("time_window.since must be on or before until")
        if (self.until - self.since).days > _MAX_WINDOW_DAYS:
            raise ValueError(f"time_window may span at most {_MAX_WINDOW_DAYS} days")
        return self


class LabsReadRequest(BaseModel):
    """Exact request shape for the governed ``labs.read`` v1 operation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    turn_request_ref: str = Field(min_length=8, max_length=256)
    actor_ref: str = Field(min_length=8, max_length=160)
    tenant_ref: str = Field(min_length=3, max_length=160)
    record_ref: str = Field(min_length=3, max_length=160)
    purpose: LabsPurpose
    categories: tuple[LabsCategory, ...] = Field(min_length=1, max_length=1)
    analytes: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    time_window: LabsTimeWindow
    tier: LabsTier

    @field_validator("turn_request_ref", "actor_ref", "tenant_ref", "record_ref")
    @classmethod
    def _safe_reference(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned != value or not _OPAQUE_REF_RE.fullmatch(cleaned):
            raise ValueError("references must be opaque tokens")
        return cleaned

    @field_validator("analytes")
    @classmethod
    def _normalized_analytes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(value.strip() for value in values)
        if any(not value or len(value) > 80 for value in cleaned):
            raise ValueError("analytes must be non-empty strings of at most 80 characters")
        if any(not _ANALYTE_RE.fullmatch(value) for value in cleaned):
            raise ValueError("analytes must be LOINC codes or bounded display names")
        if any(_INSTRUCTION_TEXT_RE.search(value) for value in cleaned):
            raise ValueError("analytes must not contain instruction text")
        if len(cleaned) != len(set(value.casefold() for value in cleaned)):
            raise ValueError("analytes must be unique")
        return cleaned

    @model_validator(mode="after")
    def _laboratory_only(self) -> "LabsReadRequest":
        if self.categories != (LabsCategory.LABORATORY,):
            raise ValueError("labs.read accepts only the laboratory category")
        return self


class LabResult(BaseModel):
    """Allowlisted discrete value returned by ``labs.read``."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    analyte: str = Field(min_length=1, max_length=120)
    loinc: Optional[str] = Field(default=None, max_length=32)
    value: str = Field(min_length=1, max_length=80)
    unit: Optional[str] = Field(default=None, max_length=32)
    reference_range: Optional[str] = Field(default=None, max_length=80)
    abnormal_flag: Optional[str] = Field(default=None, max_length=24)
    observed_time: str = Field(min_length=10, max_length=40)
    report_status: Optional[str] = Field(default=None, max_length=24)

    @field_validator("analyte")
    @classmethod
    def _safe_analyte(cls, value: str) -> str:
        if (
            value != value.strip()
            or not _ANALYTE_RE.fullmatch(value)
            or _INSTRUCTION_TEXT_RE.search(value)
        ):
            raise ValueError("analyte is not a bounded display name")
        return value

    @field_validator("loinc")
    @classmethod
    def _safe_loinc(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not re.fullmatch(r"\d{1,8}-\d", value):
            raise ValueError("invalid LOINC")
        return value

    @field_validator("value")
    @classmethod
    def _discrete_value_only(cls, value: str) -> str:
        cleaned = value.strip()
        if (
            cleaned != value
            or not (
                _NUMERIC_VALUE_RE.fullmatch(cleaned)
                or cleaned.casefold() in _QUALITATIVE_VALUES
            )
        ):
            raise ValueError("value is not a bounded discrete lab result")
        return cleaned

    @field_validator("unit")
    @classmethod
    def _safe_unit(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and (
            value != value.strip() or not _UNIT_RE.fullmatch(value)
        ):
            raise ValueError("invalid unit")
        return value

    @field_validator("reference_range")
    @classmethod
    def _safe_range(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        if not (
            cleaned == value
            and (
                _NUMERIC_VALUE_RE.fullmatch(cleaned)
                or cleaned.casefold() in _QUALITATIVE_VALUES
            )
        ):
            raise ValueError("reference range is not discrete")
        return cleaned

    @field_validator("abnormal_flag")
    @classmethod
    def _safe_flag(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = _normalize_flag(value)
        if normalized not in _FLAGS:
            raise ValueError("invalid abnormal flag")
        return normalized

    @field_validator("observed_time")
    @classmethod
    def _iso_observed_time(cls, value: str) -> str:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("observed_time must be ISO formatted") from None
        return value

    @field_validator("report_status")
    @classmethod
    def _safe_report_status(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value.casefold() not in _REPORT_STATUSES:
            raise ValueError("invalid report status")
        return value.casefold() if value is not None else None


class LabsReadResponse(BaseModel):
    """Allowlisted tool response; presentation summaries are derived later."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    record_ref: str = Field(min_length=3, max_length=160)
    results: tuple[LabResult, ...] = Field(max_length=_MAX_RESULTS)
    result_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _count_matches(self) -> "LabsReadResponse":
        if self.result_count != len(self.results):
            raise ValueError("result_count must match results")
        return self

    @field_validator("record_ref")
    @classmethod
    def _opaque_record_ref(cls, value: str) -> str:
        if not _OPAQUE_REF_RE.fullmatch(value):
            raise ValueError("record_ref must be an opaque token")
        return value


@dataclass(frozen=True)
class LabsReadAccess:
    """Server-only proof bound to the resolved mount and current ingress turn."""

    trace_id: str
    expected_turn_request_ref: str
    authorized_record_ref: str
    expected_purpose: LabsPurpose
    expected_categories: tuple[LabsCategory, ...]
    expected_time_window: LabsTimeWindow
    expected_tier: LabsTier
    mount: MountContext
    profile: ComplianceProfile
    audit: AuditLogger
    record_scope_source: Literal["host_open_record", "platform"]


def _mock_payload(as_of: date) -> list[LabReport]:
    """Deterministic Preview-shaped data for credential-free tests."""
    from app.clients.athena import LabValue

    older = (as_of - timedelta(days=180)).isoformat() + "T00:00:00Z"
    recent = (as_of - timedelta(days=30)).isoformat() + "T00:00:00Z"
    voided_at = (as_of - timedelta(days=29)).isoformat() + "T00:00:00Z"

    first = LabReport(
        report_id="mock-r1",
        name="Hemoglobin A1c",
        status="final",
        collected_at=older,
    )
    first.values = [
        LabValue(
            name="Hemoglobin A1C", loinc="4548-4", value="7.6", unit="%",
            reference_range="4.0-5.6", abnormal_flag="high",
            observed_at=older, source_id="mock-o1",
        )
    ]
    second = LabReport(
        report_id="mock-r2",
        name="Comprehensive metabolic panel",
        status="unknown",
        collected_at=recent,
    )
    second.values = [
        LabValue(
            name="Hemoglobin A1C", loinc="4548-4", value="8.2", unit="%",
            reference_range="4.0-5.6", abnormal_flag="high",
            observed_at=recent, source_id="mock-o2",
        ),
        LabValue(
            name="Ferritin", loinc=None, value="18", unit="ng/mL",
            reference_range="20-200", abnormal_flag="low",
            observed_at=recent, source_id="mock-o3",
        ),
        LabValue(
            name="Sodium", loinc="2951-2", value="140", unit="mmol/L",
            reference_range="136-145", abnormal_flag="normal",
            observed_at=recent, source_id="mock-o4",
        ),
    ]
    voided = LabReport(
        report_id="mock-r3", name="Voided panel", status="entered-in-error",
        collected_at=voided_at,
    )
    return [first, second, voided]


class LabsClient:
    def __init__(self, settings: Optional[Settings] = None):
        self.s = settings or get_settings()
        self._source_cache: Any = None

    @property
    def enabled(self) -> bool:
        return bool(self.s.feature_athena_labs)

    @property
    def sandbox(self) -> bool:
        return self.s.athena_env == "preview"

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise LabsUnavailable(
                "The lab-results capability is not enabled on this deployment."
            )

    def _source(self):
        if self._source_cache is None:
            if not (self.s.athena_client_id and self.s.athena_client_secret):
                raise LabsUnavailable(
                    "Lab results are enabled but sandbox credentials are not configured."
                )
            config = AthenaSettings(
                client_id=self.s.athena_client_id,
                client_secret=self.s.athena_client_secret,
                env=self.s.athena_env,
                practice_id=self.s.athena_practice_id,
                department_id=self.s.athena_department_id,
            )
            assert_safe_outbound_url(config.token_url)
            assert_safe_outbound_url(config.fhir_base)
            self._source_cache = AthenaFhirLabSource(
                config, TokenProvider(config, FHIR_LABS_READ_SCOPES)
            )
        return self._source_cache

    def _authorize(self, request: LabsReadRequest, access: LabsReadAccess) -> None:
        mount = access.mount
        if (
            access.profile.persona != Persona.PROVIDER
            or mount.surface != "provider_assistant"
            or mount.auth_tier != "provider"
            or "ehr:labs:read" not in mount.capabilities
            or mount.actor_id is None
            or mount.tenant_id is None
        ):
            raise LabsNotAuthorized(
                "Lab review requires an authenticated provider on the provider assistant."
            )
        if (
            request.turn_request_ref != access.expected_turn_request_ref
            or request.actor_ref != mount.actor_id
            or request.tenant_ref != mount.tenant_id
            or request.record_ref != access.authorized_record_ref
            or request.purpose != access.expected_purpose
            or request.categories != access.expected_categories
            or request.time_window != access.expected_time_window
            or request.tier != access.expected_tier
        ):
            raise LabsNotAuthorized(
                "The lab request is not bound to the current provider turn and open record."
            )
        if request.tier == LabsTier.PRODUCTION:
            raise LabsProductionUnavailable(PRODUCTION_REQUIREMENTS_MESSAGE)
        if access.record_scope_source != "host_open_record":
            raise LabsNotAuthorized(
                "Sandbox lab review requires the record currently open in the host."
            )
        if self.s.athena_env != "preview" or self.s.athena_practice_id != "195900":
            raise LabsNotAuthorized(
                "Sandbox lab review is available only in athenahealth Preview practice 195900."
            )
        self._require_enabled()

    async def find_patient(self, *_args, **_kwargs) -> dict:
        """Name search is outside ``labs.read`` and refused at every tier."""
        raise LabsNotAuthorized(
            "Patient search by name is unavailable. Open the synthetic sandbox "
            "record in the host and ask again."
        )

    async def _reports(self, request: LabsReadRequest) -> list[LabReport]:
        if self.s.mock_mode:
            return _mock_payload(request.time_window.until)
        return await asyncio.to_thread(
            self._source().get_lab_reports,
            request.record_ref,
            request.time_window.since.isoformat(),
            request.time_window.until.isoformat(),
            request.analytes,
        )

    async def read(
        self, request: LabsReadRequest, access: LabsReadAccess
    ) -> LabsReadResponse:
        """Perform one authorized, bounded and audited logical ``labs.read``."""
        self._authorize(request, access)
        access.audit.record(
            trace_id=access.trace_id,
            profile=access.profile,
            event="ehr_labs_read_before",
            details=_audit_before_details(request),
        )
        try:
            reports = await self._reports(request)
            response = _allowlisted_response(request, reports)
        except asyncio.CancelledError:
            # ``to_thread`` cannot stop an already-running synchronous FHIR
            # request. Record the unknown/cancelled outcome before propagating
            # cancellation so every initiated read still has an after event.
            access.audit.record(
                trace_id=access.trace_id,
                profile=access.profile,
                event="ehr_labs_read_after",
                details={"outcome": "cancelled", "result_count": 0},
            )
            raise
        except Exception:
            # Leave the source exception scope before raising anything. If the
            # outcome audit also fails, no exception chain may retain a FHIR
            # URL, record reference, or upstream payload detail.
            source_failed = True
        else:
            source_failed = False

        if source_failed:
            outcome_audit_failed = False
            try:
                access.audit.record(
                    trace_id=access.trace_id,
                    profile=access.profile,
                    event="ehr_labs_read_after",
                    details={"outcome": "error", "result_count": 0},
                )
            except Exception:
                outcome_audit_failed = True
            if outcome_audit_failed:
                raise LabsUnavailable(
                    "The sandbox lab read failed closed because its outcome "
                    "could not be audited."
                )
            raise LabsUnavailable(
                "I couldn't reach the sandbox lab system just now. Please try again shortly."
            )

        access.audit.record(
            trace_id=access.trace_id,
            profile=access.profile,
            event="ehr_labs_read_after",
            details={
                "outcome": "success" if response.result_count else "empty",
                "result_count": response.result_count,
            },
        )
        return response


def _audit_before_details(request: LabsReadRequest) -> dict[str, Any]:
    record_digest = hashlib.sha256(
        f"{request.tenant_ref}\0{request.record_ref}".encode("utf-8")
    ).hexdigest()
    return {
        "actor_ref": request.actor_ref,
        "tenant_ref": request.tenant_ref,
        "record_ref": f"sha256:{record_digest}",
        "turn_request_ref": request.turn_request_ref,
        "purpose": request.purpose.value,
        "categories": [category.value for category in request.categories],
        "time_window": {
            "since": request.time_window.since.isoformat(),
            "until": request.time_window.until.isoformat(),
        },
        "adapter": _ADAPTER_NAME,
    }


def _allowlisted_response(
    request: LabsReadRequest, reports: list[LabReport]
) -> LabsReadResponse:
    results: list[LabResult] = []
    for report in reports:
        if (report.status or "").casefold() == "entered-in-error":
            continue
        for value in report.values:
            if (value.status or "").casefold() == "entered-in-error":
                continue
            observed = value.observed_at or report.collected_at or report.resulted_at
            if value.value is None or not _inside_window(observed, request.time_window):
                continue
            if request.analytes and not _matches_any_analyte(
                value.name or "", value.loinc, request.analytes
            ):
                continue
            try:
                result = LabResult(
                    analyte=value.name or "Unknown analyte",
                    loinc=value.loinc,
                    value=str(value.value),
                    unit=value.unit,
                    reference_range=value.reference_range,
                    abnormal_flag=value.abnormal_flag,
                    observed_time=observed,
                    report_status=value.status or report.status,
                )
            except ValidationError:
                # Narrative/invalid rows are not discrete lab values and never
                # reach either the BAA model or the tool response.
                continue
            results.append(result)
            if len(results) > _MAX_RESULTS:
                raise ValueError("bounded lab-result limit exceeded")
    results.sort(key=lambda result: result.observed_time)
    return LabsReadResponse(
        record_ref=request.record_ref,
        results=tuple(results),
        result_count=len(results),
    )


def _inside_window(value: Optional[str], window: LabsTimeWindow) -> bool:
    if not value:
        return False
    try:
        observed = date.fromisoformat(value[:10])
    except ValueError:
        return False
    return window.since <= observed <= window.until


def _matches_any_analyte(
    name: str, loinc: Optional[str], analytes: tuple[str, ...]
) -> bool:
    folded_name = name.casefold()
    return any(
        (loinc is not None and loinc == analyte)
        or analyte.casefold() in folded_name
        for analyte in analytes
    )


def is_abnormal(flag: Optional[str]) -> bool:
    return bool(flag) and flag.casefold() not in {"normal", "n"}


def _normalize_flag(value: str) -> str:
    folded = re.sub(r"[\s_-]+", " ", value.strip().casefold())
    if folded in {"n", "normal"}:
        return "normal"
    if folded in {"h", "high"}:
        return "high"
    if folded in {"l", "low"}:
        return "low"
    if folded in {"hh", "ll", "critical", "critical high", "critical low"}:
        return "critical"
    if folded in {"a", "aa", "abnormal"}:
        return "abnormal"
    return folded


def abnormal_summary(response: LabsReadResponse) -> dict:
    """Derive the abnormal presentation without another EHR read."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for result in response.results:
        if is_abnormal(result.abnormal_flag):
            grouped[result.analyte].append(
                {
                    "observed_at": result.observed_time,
                    "value": result.value,
                    "unit": result.unit,
                    "reference_range": result.reference_range,
                    "flag": result.abnormal_flag,
                    "report_status": result.report_status,
                }
            )
    summary = []
    for name, occurrences in grouped.items():
        occurrences.sort(key=lambda item: item["observed_at"])
        summary.append(
            {
                "analyte": name,
                "occurrences": len(occurrences),
                "latest": occurrences[-1],
                "history": occurrences,
            }
        )
    summary.sort(key=lambda item: item["latest"]["observed_at"], reverse=True)
    return {"abnormal_analytes": len(summary), "summary": summary}


def analyte_trend(response: LabsReadResponse, analyte: str) -> dict:
    """Derive one oldest-first analyte series without another EHR read."""
    points = [
        {
            "observed_at": result.observed_time,
            "value": result.value,
            "unit": result.unit,
            "reference_range": result.reference_range,
            "abnormal_flag": result.abnormal_flag,
            "analyte_name": result.analyte,
            "loinc": result.loinc,
            "report_status": result.report_status,
        }
        for result in response.results
        if _matches_any_analyte(result.analyte, result.loinc, (analyte,))
    ]
    points.sort(key=lambda point: point["observed_at"])
    return {"analyte_query": analyte, "point_count": len(points), "points": points}
