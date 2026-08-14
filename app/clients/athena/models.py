"""Cosmaneura-internal normalized lab records.

This is the shape the Cosmaneura UI/analytics layer consumes. Every EMR
adapter (athenahealth FHIR, athenahealth proprietary, future Epic/eCW/...)
normalizes into these, so the product never depends on one EMR's schema.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LabValue:
    """A single analyte/observation, e.g. one row of a CMP."""

    name: str                       # e.g. "Potassium"
    loinc: str | None = None        # e.g. "2823-3" — key for cross-EMR trending
    value: str | None = None        # numeric or string result as reported
    unit: str | None = None         # e.g. "mmol/L"
    reference_range: str | None = None  # e.g. "3.5-5.1"
    abnormal_flag: str | None = None    # normal/low/high/critical... (source-reported)
    status: str | None = None       # final/preliminary/corrected
    observed_at: str | None = None  # ISO 8601 when available
    source_id: str | None = None    # EMR-native id for traceability/audit


@dataclass
class LabReport:
    """A lab result document/report (often a panel) containing values."""

    report_id: str                  # EMR-native report id
    name: str | None = None         # e.g. "Comprehensive metabolic panel"
    loinc: str | None = None        # panel-level LOINC when reported
    status: str | None = None
    collected_at: str | None = None
    resulted_at: str | None = None
    performing_lab: str | None = None
    has_document: bool = False      # PDF/image available (fetch via adapter)
    values: list[LabValue] = field(default_factory=list)
    source_system: str = "athenahealth"
    raw: dict | None = None         # original payload for debugging (drop in prod logs)
