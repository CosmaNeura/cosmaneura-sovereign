"""LabSource adapter over athenahealth's Certified FHIR R4 APIs.

This adapter is deliberately limited to DiagnosticReport and Observation.
Patient search, documents, and every other chart resource are outside the
governed ``labs.read`` operation.

Docs: docs.athenahealth.com/api/fhir-r4/{diagnostic-report,observation}
"""
from __future__ import annotations

import re

import requests

from .adapter import LabSource
from .auth import TokenProvider
from .config import Settings
from .models import LabReport, LabValue

US_CORE_LAB_CATEGORY = "http://terminology.hl7.org/CodeSystem/v2-0074|LAB"
OBS_LAB_CATEGORY = (
    "http://terminology.hl7.org/CodeSystem/observation-category|laboratory"
)
_MAX_FHIR_PAGES = 50
_MAX_FHIR_SEARCH_ENTRIES = 2_000
_PAGING_QUERY_KEYS = frozenset(
    {"_getpages", "_getpagesoffset", "_count", "cursor", "page", "offset"}
)
_REPORT_ELEMENTS = (
    "id,status,effectiveDateTime,effectivePeriod,issued,result"
)
_OBSERVATION_ELEMENTS = (
    "id,status,code,valueQuantity,valueString,valueCodeableConcept,"
    "referenceRange,interpretation,effectiveDateTime,issued"
)


class AthenaFhirLabSource(LabSource):
    def __init__(self, settings: Settings, tokens: TokenProvider):
        self.s = settings
        self.tokens = tokens

    # ---------- low-level ----------
    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = requests.get(
            f"{self.s.fhir_base}/{path.lstrip('/')}",
            params=params,
            headers={**self.tokens.auth_headers(), "Accept": "application/fhir+json"},
            timeout=60,
        )
        if resp.status_code != 200:
            # Vendoring delta: the response body is dropped. FHIR error
            # payloads can echo patient data, and this repo treats PHI in
            # error paths as a security defect.
            raise RuntimeError(f"FHIR GET {path} -> {resp.status_code}")
        return resp.json()

    def _search_all(self, resource: str, params: dict) -> list[dict]:
        """Run a FHIR search and follow 'next' bundle links (cursor paging)."""
        from urllib.parse import parse_qs, urlparse

        entries: list[dict] = []
        bundle = self._get(resource, params)
        expected_origin = urlparse(self.s.fhir_base)
        expected_path = f"{expected_origin.path.rstrip('/')}/{resource}"
        pages = 0
        seen_next_urls: set[str] = set()
        while True:
            pages += 1
            page_resources = [
                entry["resource"]
                for entry in bundle.get("entry", [])
                if "resource" in entry
            ]
            if any(item.get("resourceType") != resource for item in page_resources):
                raise RuntimeError("FHIR bundle contained an unexpected resource type")
            entries += page_resources
            if len(entries) > _MAX_FHIR_SEARCH_ENTRIES:
                raise RuntimeError("FHIR bounded search entry limit exceeded")
            next_url = next(
                (l["url"] for l in bundle.get("link", []) if l.get("relation") == "next"),
                None,
            )
            if not next_url:
                return entries
            if next_url in seen_next_urls or pages >= _MAX_FHIR_PAGES:
                raise RuntimeError("FHIR bounded paging limit exceeded")
            seen_next_urls.add(next_url)
            # The next link is server-supplied data carrying our bearer token.
            # Require the same HTTPS origin/resource and every original search
            # restriction. A cursor may be added; scope may never be removed.
            parsed_next = urlparse(next_url)
            try:
                same_origin = (
                    parsed_next.scheme == "https"
                    and parsed_next.hostname == expected_origin.hostname
                    and (parsed_next.port or 443) == (expected_origin.port or 443)
                    and parsed_next.username is None
                    and parsed_next.password is None
                )
            except ValueError:
                same_origin = False
            next_query = parse_qs(parsed_next.query, keep_blank_values=True)
            preserves_scope = all(
                sorted(next_query.get(key, [])) == sorted(_query_values(value))
                for key, value in params.items()
            )
            query_keys_are_bounded = set(next_query).issubset(
                set(params) | _PAGING_QUERY_KEYS
            )
            if (
                not same_origin
                or parsed_next.path != expected_path
                or parsed_next.fragment
                or not preserves_scope
                or not query_keys_are_bounded
            ):
                raise RuntimeError("FHIR paging link changed the bounded search; refusing")
            resp = requests.get(
                next_url,
                headers={**self.tokens.auth_headers(), "Accept": "application/fhir+json"},
                timeout=60,
            )
            if resp.status_code != 200:
                # Do not include the paging URL: its query contains the record
                # reference and could turn an operational error into a PHI log.
                raise RuntimeError(
                    f"FHIR paged search failed ({resp.status_code})"
                )
            bundle = resp.json()

    # ---------- LabSource ----------
    def get_lab_reports(self, record_ref, since, until, analytes=()):
        params = {
            "patient": record_ref,
            "category": US_CORE_LAB_CATEGORY,
            "ah-practice": self.s.ah_practice,
            "date": [f"ge{since}", f"le{until}"],
            "_elements": _REPORT_ELEMENTS,
        }
        reports = self._search_all("DiagnosticReport", params)

        # Pull the patient's lab Observations once, index by id for fast joining.
        obs_params = {
            "patient": record_ref,
            "category": OBS_LAB_CATEGORY,
            "ah-practice": self.s.ah_practice,
            "date": [f"ge{since}", f"le{until}"],
            "_elements": _OBSERVATION_ELEMENTS,
        }
        # FHIR token search can narrow by LOINC. Display-name requests are still
        # filtered locally because athena Preview does not guarantee :text.
        loinc_codes = [value for value in analytes if _looks_like_loinc(value)]
        if loinc_codes:
            obs_params["code"] = ",".join(loinc_codes)
        observations = self._search_all("Observation", obs_params)
        observations = [
            observation
            for observation in observations
            if (observation.get("status") or "").casefold() != "entered-in-error"
        ]
        obs_by_id = {o["id"]: o for o in observations if o.get("id")}

        out = []
        for r in reports:
            if (r.get("status") or "").casefold() == "entered-in-error":
                continue
            report = LabReport(
                report_id=r.get("id", ""),
                status=r.get("status"),
                collected_at=r.get("effectiveDateTime")
                or (r.get("effectivePeriod") or {}).get("start"),
                resulted_at=r.get("issued"),
            )
            for ref in r.get("result", []):
                obs_id = (ref.get("reference") or "").split("/")[-1]
                # Never follow an id missing from the bounded Observation
                # search; doing so would bypass its since/until constraint.
                obs = obs_by_id.get(obs_id)
                if obs:
                    report.values.append(_observation_to_value(obs))
            out.append(report)

        # Standalone lab observations not tied to a fetched report are still
        # valuable for trending; surface them as a synthetic report.
        seen = {v.source_id for rep in out for v in rep.values}
        orphans = [
            _observation_to_value(o)
            for o in observations
            if o.get("id") and o["id"] not in seen
        ]
        if orphans:
            out.append(LabReport(report_id="unattached-observations",
                                 name="Lab observations without a report",
                                 values=orphans))
        return out


# ---------- FHIR helpers ----------

def _first_loinc(codeable: dict) -> str | None:
    for c in codeable.get("coding", []):
        if "loinc" in (c.get("system") or ""):
            return c.get("code")
    return None


def _first_coding_display(codeable: dict) -> str | None:
    for c in codeable.get("coding", []):
        if c.get("display"):
            return c["display"]
    return None


def _first_coding_code(codeable: dict) -> str | None:
    for coding in codeable.get("coding", []):
        if coding.get("code"):
            return coding["code"]
    return None


def _looks_like_loinc(value: str) -> bool:
    left, separator, right = value.partition("-")
    return bool(separator and left.isdigit() and right.isdigit())


def _query_values(value) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def _observation_to_value(obs: dict) -> LabValue:
    vq = obs.get("valueQuantity") or {}
    value = (
        str(vq.get("value")) if vq.get("value") is not None
        else obs.get("valueString")
        or (obs.get("valueCodeableConcept") or {}).get("text")
    )
    rr = (obs.get("referenceRange") or [{}])[0]
    ref = _normalized_reference_range(rr)
    interp = (obs.get("interpretation") or [{}])[0]
    return LabValue(
        name=(obs.get("code", {}).get("text") or _first_coding_display(obs.get("code", {})) or "?"),
        loinc=_first_loinc(obs.get("code", {})),
        value=value,
        unit=vq.get("unit") or vq.get("code"),
        reference_range=ref,
        abnormal_flag=_normalized_interpretation(
            interp.get("text")
            or _first_coding_display(interp)
            or _first_coding_code(interp)
        ),
        status=obs.get("status"),
        observed_at=obs.get("effectiveDateTime") or obs.get("issued"),
        source_id=obs.get("id"),
    )


def _normalized_reference_range(reference: dict) -> str | None:
    """Prefer structured low/high and narrowly normalize safe numeric text."""
    low = (reference.get("low") or {}).get("value")
    high = (reference.get("high") or {}).get("value")
    if low is not None and high is not None:
        return f"{low}-{high}"
    if low is not None:
        return f">={low}"
    if high is not None:
        return f"<={high}"
    text = reference.get("text")
    if not isinstance(text, str):
        return None
    match = re.fullmatch(
        r"\s*([<>]=?\s*[-+]?\d+(?:\.\d+)?|"
        r"[-+]?\d+(?:\.\d+)?\s*[-–]\s*[-+]?\d+(?:\.\d+)?)"
        r"(?:\s*[A-Za-z%µμ./^*_-]+)?\s*",
        text,
    )
    return re.sub(r"\s+", "", match.group(1)) if match else None


def _normalized_interpretation(value: str | None) -> str | None:
    if not value:
        return None
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
