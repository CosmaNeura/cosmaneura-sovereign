"""Explicit Core 1.1 facts receiver; references remain server-only lineage."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import Field, StrictBool, field_validator, model_validator

from app.clients.session_prep_contract import (
    ClosedModel, Digest, OpaqueRef, Version, SessionPrepContextReadResponse,
    SessionPrepContextReadResult, _aware, _timestamp_wire,
)

MAX_FACTS = 48
FACT_STATES = {
    "medication": {"planned", "documented_taking", "documented_continued",
                   "documented_started", "documented_prescribed", "documented_listed",
                   "documented_not_taking", "stopped", "superseded"},
    "order": {"ordered", "resulted", "cancelled", "not_ordered",
              "order_not_documented", "result_not_documented"},
    "vital": {"measured"},
}
FactState = Literal[
    "planned", "documented_taking", "documented_continued", "documented_started",
    "documented_prescribed", "documented_listed", "documented_not_taking", "stopped",
    "superseded", "ordered", "resulted", "cancelled", "not_ordered",
    "order_not_documented", "result_not_documented", "measured",
]


class SessionPrepFactsArguments(ClosedModel):
    source_turn_ref: OpaqueRef
    context_snapshot_ref: OpaqueRef
    purpose: Literal["session_prep"] = "session_prep"
    detail: Literal["structured_facts"] = "structured_facts"


class SessionPrepFactsRequest(ClosedModel):
    tool_name: Literal["session-prep.context.read"] = "session-prep.context.read"
    tool_version: Literal["1.1.0"] = "1.1.0"
    subject_ref: None = None
    arguments: SessionPrepFactsArguments


class FactSource(ClosedModel):
    source_ref: OpaqueRef
    source_version: Version
    source_at: datetime

    _wire_date = field_validator("source_at", mode="before")(_timestamp_wire)
    _aware_date = field_validator("source_at")(_aware)


class FactEvidence(FactSource):
    section: str = Field(min_length=1, max_length=160)
    quote: str = Field(min_length=1, max_length=1200, repr=False)
    start: int = Field(strict=True, ge=0, le=18000)
    end: int = Field(strict=True, ge=1, le=18000)

    @model_validator(mode="after")
    def span_is_exact(self) -> FactEvidence:
        if self.end - self.start != len(self.quote):
            raise ValueError("Evidence span length is inconsistent")
        return self


class DocumentedFact(ClosedModel):
    fact_ref: OpaqueRef
    kind: Literal["medication", "order", "vital"]
    subject: str = Field(min_length=1, max_length=120)
    value: Optional[str] = Field(min_length=1, max_length=120)
    state: FactState
    setting: Optional[Literal["office", "home", "unspecified"]]
    evidence: FactEvidence
    supersedes: list[OpaqueRef] = Field(max_length=MAX_FACTS)
    correction_evidence: Optional[FactEvidence]

    @model_validator(mode="after")
    def exact_observation_shape(self) -> DocumentedFact:
        if self.state not in FACT_STATES[self.kind] or (
            (self.kind == "vital") != (self.setting is not None)
        ) or (self.kind == "vital" and self.value is None):
            raise ValueError("Fact kind and documented status are inconsistent")
        def fold(text: str) -> str:
            return " ".join(text.casefold().split())
        if fold(self.subject) not in fold(self.evidence.quote) or (
            self.value is not None and self.value not in self.evidence.quote
        ):
            raise ValueError("Fact value is absent from its quotation")
        return self


class StructuredFactSet(ClosedModel):
    schema_version: Literal["1.0.0"]
    coverage: Literal["bounded_explicit_mentions"]
    source_count: int = Field(strict=True, ge=0, le=5)
    selection_limited: StrictBool
    facts_limited: StrictBool
    sources: list[FactSource] = Field(max_length=5)
    facts: list[DocumentedFact] = Field(max_length=MAX_FACTS)

    @model_validator(mode="after")
    def exact_lineage(self) -> StructuredFactSet:
        if len(self.model_dump_json()) > 80000:
            raise ValueError("Fact set exceeds its character bound")
        sources = {source.source_ref: source for source in self.sources}
        facts = {fact.fact_ref: fact for fact in self.facts}
        if len(sources) != len(self.sources) or len(sources) != self.source_count or (
            len(facts) != len(self.facts)
        ):
            raise ValueError("Sources and facts must be unique and counted exactly")
        for fact in self.facts:
            for evidence in (fact.evidence, fact.correction_evidence):
                if evidence is None:
                    continue
                source = sources.get(evidence.source_ref)
                if source is None or (source.source_version, source.source_at) != (
                    evidence.source_version, evidence.source_at,
                ):
                    raise ValueError("Fact evidence must match reviewed source lineage")
            if len(fact.supersedes) != len(set(fact.supersedes)) or (
                bool(fact.supersedes) != (fact.correction_evidence is not None)
            ):
                raise ValueError("Corrections require unique references and evidence")
            if fact.correction_evidence is not None and (
                fact.correction_evidence.source_ref, fact.correction_evidence.source_version,
                fact.correction_evidence.source_at,
            ) != (fact.evidence.source_ref, fact.evidence.source_version, fact.evidence.source_at):
                raise ValueError("Correction evidence must match its later observation")
            for ref in fact.supersedes:
                prior = facts.get(ref)
                if prior is None or prior.kind != fact.kind or (
                    prior.subject.casefold() != fact.subject.casefold()
                    or not (prior.evidence.source_at < fact.evidence.source_at or (
                        prior.evidence.source_at == fact.evidence.source_at
                        and prior.evidence.source_ref == fact.evidence.source_ref
                        and prior.evidence.source_version == fact.evidence.source_version
                        and prior.evidence.section == fact.evidence.section
                        and prior.evidence.end <= fact.evidence.start
                    ))
                ):
                    raise ValueError("Correction must cite an earlier matching observation")
        return self


class SessionPrepFactsResult(ClosedModel):
    schema_version: Literal["1.1.0"]
    preparation: SessionPrepContextReadResult
    source_version_digest: Digest
    facts_state: Literal["ready", "unavailable"]
    reason_code: Optional[Literal["no_eligible_sources"]]
    structured_facts: Optional[StructuredFactSet]

    @model_validator(mode="after")
    def consistent(self) -> SessionPrepFactsResult:
        facts = self.structured_facts
        if self.facts_state == "unavailable":
            if facts is not None or self.reason_code != "no_eligible_sources":
                raise ValueError("Unavailable sources cannot carry facts")
        elif facts is None or facts.source_count == 0 or self.reason_code is not None:
            raise ValueError("Available facts require reviewed sources")
        if self.preparation.state in {"ready", "refreshing"}:
            if facts is None or (self.source_version_digest, facts.source_count) != (
                self.preparation.source_version_digest, self.preparation.source_count,
            ):
                raise ValueError("Preparation and facts must use the same source selection")
            expected = {p.source_ref: (p.source_version, p.source_at)
                        for p in self.preparation.provenance if p.included}
            if expected != {s.source_ref: (s.source_version, s.source_at) for s in facts.sources}:
                raise ValueError("Preparation and fact source provenance differ")
        return self


class SessionPrepFactsReadResponse(SessionPrepContextReadResponse):
    tool_version: Literal["1.1.0"] = "1.1.0"  # type: ignore[assignment]
    result: SessionPrepFactsResult  # type: ignore[assignment]
