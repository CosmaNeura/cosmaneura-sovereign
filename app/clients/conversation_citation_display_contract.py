"""Closed accepted-map display contracts; never inference or source authority."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, StrictInt, field_validator, model_validator

from app.clients.conversation_read import (
    ConversationReadRequest,
    ConversationReadResponse,
    HistoryMessage,
    HydratedHistory,
)
from app.clients.session_prep_contract import (
    ClosedModel,
    OpaqueRef,
    _aware,
    _timestamp_wire,
)


class DisplayCitation(ClosedModel):
    citation_ref: OpaqueRef
    ordinal: StrictInt = Field(ge=1, le=64)
    source_at: datetime
    reviewed_at: datetime
    section: str = Field(strict=True, min_length=1, max_length=200)

    _wire = field_validator("source_at", "reviewed_at", mode="before")(_timestamp_wire)
    _aware_time = field_validator("source_at", "reviewed_at")(_aware)

    @model_validator(mode="after")
    def _reference(self):
        prefix, separator, ordinal = self.citation_ref.rpartition(".")
        if not separator or len(prefix) < 8 or ordinal != str(self.ordinal):
            raise ValueError("invalid accepted citation reference")
        return self


def _map(citations: list[DisplayCitation]) -> None:
    if citations:
        prefix = citations[0].citation_ref.rpartition(".")[0]
        if [item.ordinal for item in citations] != list(
            range(1, len(citations) + 1)
        ) or any(item.citation_ref != f"{prefix}.{item.ordinal}" for item in citations):
            raise ValueError("invalid original citation order")


class CitationReadRequest(ConversationReadRequest):
    schema_version: Literal["1.1.0"]


class RetainedDisplayRequest(ConversationReadRequest):
    schema_version: Literal["1.2.0"]


class CitationMessage(HistoryMessage):
    reviewed_citations: list[DisplayCitation] = Field(max_length=64)

    @model_validator(mode="after")
    def _display(self):
        if self.role == "user" and self.reviewed_citations:
            raise ValueError("user cannot carry accepted citations")
        _map(self.reviewed_citations)
        return self


class RetainedDisplayMessage(HistoryMessage):
    reviewed_citations: list[DisplayCitation] | None = Field(max_length=64)
    display_state: Literal["historical", "superseded"] | None

    @model_validator(mode="after")
    def _display(self):
        if self.role == "user":
            if self.reviewed_citations is not None or self.display_state is not None:
                raise ValueError("user cannot carry retained display fields")
        elif self.reviewed_citations is None or self.display_state is None:
            raise ValueError("accepted assistant display fields missing")
        else:
            _map(self.reviewed_citations)
        return self


class CitationHistory(HydratedHistory):
    messages: list[CitationMessage] = Field(max_length=12)


class RetainedDisplayHistory(HydratedHistory):
    messages: list[RetainedDisplayMessage] = Field(max_length=12)


def _completed_maps(response) -> None:
    outcomes = {turn.client_turn_id: turn.outcome for turn in response.turns}
    if any(
        message.role == "assistant"
        and message.reviewed_citations
        and outcomes.get(message.client_turn_id) != "completed"
        for message in response.history.messages
    ):
        raise ValueError("accepted map requires completed answer")


class CitationReadResponse(ConversationReadResponse):
    schema_version: Literal["1.1.0"]
    history: CitationHistory

    @model_validator(mode="after")
    def _completed(self):
        _completed_maps(self)
        return self


class RetainedDisplayResponse(ConversationReadResponse):
    schema_version: Literal["1.2.0"]
    history: RetainedDisplayHistory

    @model_validator(mode="after")
    def _completed(self):
        _completed_maps(self)
        return self
