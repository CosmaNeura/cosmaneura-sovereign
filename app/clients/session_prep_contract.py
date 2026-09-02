"""Dormant consumer types for ``session-prep.context.read@1.0.0``.

There is intentionally no transport, dereferencing, registration, or feature
activation here. Opaque references are comparison inputs, never authority.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator
from pydantic import model_validator


OpaqueRef = Annotated[
    str, Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._~-]{8,160}$")
]
Digest = Annotated[
    str, Field(min_length=71, max_length=71, pattern=r"^sha256_[a-f0-9]{64}$")
]
LineageVersion = Annotated[
    str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
]
SourceClass = Literal["clinician_finalized_note", "patient_reported_visit_prep"]
AuthorClass = Literal["clinician", "patient"]
ProvenanceReason = Literal[
    "eligible_finalized_note",
    "eligible_patient_visit_prep",
    "no_eligible_source",
    "source_not_enabled",
    "selection_limit",
    "source_temporarily_unavailable",
]
MAX_SOURCES = 6
MAX_PROVENANCE = 8
STALE_WINDOW = timedelta(minutes=15)


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionPrepDetail(str, Enum):
    BRIEF = "brief"
    SUPPORTING_CONTEXT = "supporting_context"


class SessionPrepContextReadArguments(ClosedModel):
    source_turn_ref: OpaqueRef
    context_snapshot_ref: OpaqueRef
    purpose: Literal["session_prep"]
    detail: SessionPrepDetail


class SessionPrepContextReadRequest(ClosedModel):
    tool_name: Literal["session-prep.context.read"]
    tool_version: Literal["1.0.0"]
    subject_ref: None
    arguments: SessionPrepContextReadArguments


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("timestamp must include an offset")
    return value


def _timestamp_wire(value: object) -> object:
    if isinstance(value, str) and not 20 <= len(value) <= 35:
        raise ValueError("timestamp must contain 20 to 35 characters")
    return value


def _unique(values: list[str]) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError("source references must be unique")
    return values


class SessionPrepProvenance(ClosedModel):
    source_class: SourceClass
    included: bool
    source_ref: Optional[OpaqueRef]
    source_version: Optional[int] = Field(ge=1, le=2_147_483_647)
    source_at: Optional[datetime]
    author_class: Optional[AuthorClass]
    reason_code: ProvenanceReason

    _wire_source = field_validator("source_at", mode="before")(_timestamp_wire)
    _aware_source = field_validator("source_at")(_aware)

    @model_validator(mode="after")
    def _valid_disposition(self) -> "SessionPrepProvenance":
        lineage = (self.source_ref, self.source_version, self.source_at, self.author_class)
        expected = {
            "clinician_finalized_note": ("clinician", "eligible_finalized_note"),
            "patient_reported_visit_prep": (
                "patient",
                "eligible_patient_visit_prep",
            ),
        }
        if self.included:
            if any(value is None for value in lineage):
                raise ValueError("included provenance requires source lineage")
            if (self.author_class, self.reason_code) != expected[self.source_class]:
                raise ValueError("included provenance class and reason do not match")
        else:
            if any(value is not None for value in lineage):
                raise ValueError("excluded provenance cannot carry source lineage")
            allowed = {
                "no_eligible_source",
                "selection_limit",
                "source_temporarily_unavailable",
            }
            if self.source_class == "patient_reported_visit_prep":
                allowed.add("source_not_enabled")
            if self.reason_code not in allowed:
                raise ValueError("excluded provenance reason does not match source class")
        return self


class CitedText(ClosedModel):
    source_refs: list[OpaqueRef] = Field(min_length=1, max_length=MAX_SOURCES)

    _unique_refs = field_validator("source_refs")(_unique)


class SessionPrepBriefText(CitedText):
    text: str = Field(min_length=1, max_length=1_000)


class SessionPrepRecap(CitedText):
    text: str = Field(min_length=1, max_length=4_000)


class SessionPrepPatientReported(CitedText):
    label: Literal["Patient-reported Visit Prep"]
    summary: str = Field(min_length=1, max_length=2_000)


class SessionPrepPreparedBrief(ClosedModel):
    recap: SessionPrepRecap
    trajectory_and_material_changes: list[SessionPrepBriefText] = Field(max_length=8)
    talking_points: list[SessionPrepBriefText] = Field(min_length=1, max_length=8)
    open_threads: list[SessionPrepBriefText] = Field(max_length=8)
    cautions: list[SessionPrepBriefText] = Field(max_length=8)
    suggested_outline: list[SessionPrepBriefText] = Field(min_length=1, max_length=8)
    patient_reported: Optional[SessionPrepPatientReported]

    def cited_refs(self) -> set[str]:
        refs = set(self.recap.source_refs)
        groups = (
            self.trajectory_and_material_changes,
            self.talking_points,
            self.open_threads,
            self.cautions,
            self.suggested_outline,
        )
        for item in (item for group in groups for item in group):
            refs.update(item.source_refs)
        if self.patient_reported is not None:
            refs.update(self.patient_reported.source_refs)
        return refs


class SessionPrepSupportingExcerpt(ClosedModel):
    section: Literal[
        "history", "assessment", "plan", "medication", "safety",
        "patient_concern", "other",
    ]
    text: str = Field(min_length=1, max_length=2_000)


class SessionPrepSupportingContext(ClosedModel):
    source_ref: OpaqueRef
    source_class: SourceClass
    author_class: AuthorClass
    source_version: int = Field(ge=1, le=2_147_483_647)
    source_at: datetime
    summary: str = Field(min_length=1, max_length=2_000)
    excerpts: list[SessionPrepSupportingExcerpt] = Field(min_length=1, max_length=8)

    _wire_source = field_validator("source_at", mode="before")(_timestamp_wire)
    _aware_source = field_validator("source_at")(_aware)


class SessionPrepContentResult(ClosedModel):
    schema_version: Literal["1.0.0"]
    detail: SessionPrepDetail
    completeness: Literal["complete", "partial"]
    current_through: datetime
    snapshot_ref: OpaqueRef
    snapshot_version: int = Field(ge=1, le=2_147_483_647)
    snapshot_generated_at: datetime
    snapshot_supersedes_ref: Optional[OpaqueRef]
    source_version_digest: Digest
    brief_ref: OpaqueRef
    brief_version: int = Field(ge=1, le=2_147_483_647)
    brief_generated_at: datetime
    brief_supersedes_ref: Optional[OpaqueRef]
    brief_generator_version: LineageVersion
    brief_prompt_version: LineageVersion
    brief_model_profile: Optional[LineageVersion]
    source_count: int = Field(ge=1, le=MAX_SOURCES)
    provenance: list[SessionPrepProvenance] = Field(min_length=1, max_length=MAX_PROVENANCE)
    prepared_brief: SessionPrepPreparedBrief
    supporting_context: Optional[list[SessionPrepSupportingContext]] = Field(
        min_length=1, max_length=MAX_SOURCES,
    )

    _aware_times = field_validator(
        "current_through", "snapshot_generated_at", "brief_generated_at",
    )(_aware)
    _wire_times = field_validator(
        "current_through", "snapshot_generated_at", "brief_generated_at",
        mode="before",
    )(_timestamp_wire)

    @model_validator(mode="after")
    def _valid_content(self) -> "SessionPrepContentResult":
        if not self.current_through <= self.snapshot_generated_at <= self.brief_generated_at:
            raise ValueError("artifact timestamps do not follow source cutoff")
        for version, prior, current, name in (
            (self.snapshot_version, self.snapshot_supersedes_ref, self.snapshot_ref, "snapshot"),
            (self.brief_version, self.brief_supersedes_ref, self.brief_ref, "brief"),
        ):
            if (version == 1) != (prior is None):
                raise ValueError(f"{name} predecessor does not match version")
            if prior == current:
                raise ValueError(f"{name} cannot supersede itself")

        included = [item for item in self.provenance if item.included]
        if self.source_count != len(included):
            raise ValueError("source_count does not match included provenance")
        refs = [item.source_ref for item in included]
        if len(refs) != len(set(refs)):
            raise ValueError("included source references must be unique")
        by_ref = {item.source_ref: item for item in included}
        if any(item.source_at > self.current_through for item in included):
            raise ValueError("source timestamp exceeds current_through")
        for source_class, maximum in (
            ("clinician_finalized_note", 5), ("patient_reported_visit_prep", 1),
        ):
            class_items = [x for x in self.provenance if x.source_class == source_class]
            if sum(x.included for x in class_items) > maximum:
                raise ValueError("included provenance exceeds provider_v1 class bounds")
            excluded = sum(not x.included for x in class_items)
            if excluded > 1 or (not any(x.included for x in class_items) and excluded != 1):
                raise ValueError("invalid class-level provenance disposition")

        partial = any(
            item.reason_code in {"selection_limit", "source_temporarily_unavailable"}
            for item in self.provenance
        )
        if (self.completeness == "partial") != partial:
            raise ValueError("completeness does not match provenance")
        if not self.prepared_brief.cited_refs().issubset(by_ref):
            raise ValueError("prepared brief cites an unincluded source")
        patient = self.prepared_brief.patient_reported
        patient_refs = {
            item.source_ref for item in included
            if item.source_class == "patient_reported_visit_prep"
        }
        if patient is not None and not set(patient.source_refs).issubset(patient_refs):
            raise ValueError("patient-reported brief cites non-patient provenance")

        if (self.detail == SessionPrepDetail.BRIEF) != (self.supporting_context is None):
            raise ValueError("detail does not match supporting_context")
        supporting = self.supporting_context or []
        size = sum(
            len(item.summary) + sum(len(excerpt.text) for excerpt in item.excerpts)
            for item in supporting
        )
        if size > 8_000:
            raise ValueError("supporting context exceeds provider_v1 text budget")
        for item in supporting:
            source = by_ref.get(item.source_ref)
            if source is None or (
                item.source_class, item.author_class, item.source_version, item.source_at,
            ) != (
                source.source_class, source.author_class,
                source.source_version, source.source_at,
            ):
                raise ValueError("supporting-context provenance does not match")
        return self


class SessionPrepReadyResult(SessionPrepContentResult):
    state: Literal["ready"]
    freshness: Literal["current"]
    retry_after_seconds: None
    reason_code: None


class SessionPrepRefreshingResult(SessionPrepContentResult):
    state: Literal["refreshing"]
    freshness: Literal["stale"]
    retry_after_seconds: int = Field(ge=1, le=300)
    reason_code: Literal["refresh_in_progress"]


class SessionPrepEmptyResult(ClosedModel):
    schema_version: Literal["1.0.0"]
    detail: SessionPrepDetail
    freshness: Literal["none"]
    completeness: Literal["none"]
    current_through: None
    snapshot_ref: None
    snapshot_version: None
    snapshot_generated_at: None
    snapshot_supersedes_ref: None
    source_version_digest: None
    brief_ref: None
    brief_version: None
    brief_generated_at: None
    brief_supersedes_ref: None
    brief_generator_version: None
    brief_prompt_version: None
    brief_model_profile: None
    source_count: Literal[0]
    provenance: list[SessionPrepProvenance] = Field(max_length=0)
    prepared_brief: None
    supporting_context: None


class SessionPrepPendingResult(SessionPrepEmptyResult):
    state: Literal["pending"]
    retry_after_seconds: int = Field(ge=1, le=300)
    reason_code: Literal["generation_pending"]


class SessionPrepUnavailableResult(SessionPrepEmptyResult):
    state: Literal["unavailable"]
    retry_after_seconds: None
    reason_code: Literal["no_eligible_sources", "context_not_available"]


SessionPrepContextReadResult = Annotated[
    Union[
        SessionPrepReadyResult, SessionPrepRefreshingResult,
        SessionPrepPendingResult, SessionPrepUnavailableResult,
    ],
    Field(discriminator="state"),
]
_RESULT_ADAPTER = TypeAdapter(SessionPrepContextReadResult)


def parse_session_prep_result(value: Any) -> SessionPrepContextReadResult:
    return _RESULT_ADAPTER.validate_python(value)


def validate_session_prep_exchange(
    request: SessionPrepContextReadRequest,
    result: SessionPrepContextReadResult,
    *,
    observed_at: datetime,
) -> SessionPrepContextReadResult:
    """Apply request/result and 15-minute stale-window receiver checks."""
    _aware(observed_at)
    if request.arguments.detail != result.detail:
        raise ValueError("result detail does not match request")
    if isinstance(result, (SessionPrepReadyResult, SessionPrepRefreshingResult)):
        if result.brief_generated_at > observed_at:
            raise ValueError("brief_generated_at cannot be in future")
        if (
            isinstance(result, SessionPrepRefreshingResult)
            and result.brief_generated_at + STALE_WINDOW <= observed_at
        ):
            raise ValueError("refreshing result stale window has expired")
    return result
