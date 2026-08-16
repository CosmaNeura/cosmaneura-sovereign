"""EMR-agnostic adapter interface.

Cosmaneura code should depend on LabSource, never on a concrete EMR client.
Adding Epic/eClinicalWorks/middleware later = one new adapter class.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .models import LabReport


class LabSource(ABC):
    @abstractmethod
    def find_patient(self, family: str, given: str | None = None,
                     birthdate: str | None = None) -> list[dict]:
        """Search patients; returns [{'id', 'name', 'birthdate'}, ...]."""

    @abstractmethod
    def get_lab_reports(self, patient_id: str, since: str | None = None) -> list[LabReport]:
        """All lab reports (with discrete values where available) for a patient.

        since: ISO date (YYYY-MM-DD) lower bound, optional.
        """

    @abstractmethod
    def get_report_document(self, report: LabReport) -> bytes | None:
        """Original lab report file (PDF/image) if one exists."""
