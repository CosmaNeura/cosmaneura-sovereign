"""Optional excerpt coordinates; Core derives citation grants after acceptance."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, StrictInt, StrictStr, model_validator

from app.clients.session_prep_contract import ClosedModel


class ReviewedExcerpt(ClosedModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always", hide_input_in_errors=True)

    ordinal: StrictInt = Field(ge=1, le=64)
    source_index: StrictInt = Field(ge=0, le=4)
    section_index: StrictInt = Field(ge=0, le=63)


class ReviewedExcerpts(ClosedModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always", hide_input_in_errors=True)

    schema_version: Literal["1.0.0"]
    enumeration_sha256: StrictStr = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    mode: Literal["selected", "timeline"]
    excerpts: list[ReviewedExcerpt] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _ordered_unique(self) -> ReviewedExcerpts:
        if self.mode == "selected" and len(self.excerpts) > 8:
            raise ValueError("selected excerpt budget exceeded")
        if [item.ordinal for item in self.excerpts] != list(range(1, len(self.excerpts) + 1)):
            raise ValueError("excerpt ordinals must be contiguous")
        coordinates = [(item.source_index, item.section_index) for item in self.excerpts]
        if len(set(coordinates)) != len(coordinates):
            raise ValueError("duplicate excerpt coordinates")
        return self
