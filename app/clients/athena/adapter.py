"""EMR-agnostic adapter interface.

Cosmaneura code should depend on LabSource, never on a concrete EMR client.
Adding Epic/eClinicalWorks/middleware later = one new adapter class.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .models import LabReport


class LabSource(ABC):
    @abstractmethod
    def get_lab_reports(
        self,
        record_ref: str,
        since: str,
        until: str,
        analytes: tuple[str, ...] = (),
    ) -> list[LabReport]:
        """Bounded lab reports for one host-open record.

        Both bounds are required ISO dates. ``analytes`` may narrow the
        Observation query further when it contains LOINC codes.
        """
