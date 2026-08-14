"""LabSource adapter over athenahealth's Certified FHIR R4 APIs.

This is the production-recommended path: free, self-service, contract-less.
Resources used: Patient, DiagnosticReport (category LAB), Observation
(category laboratory), Binary (report PDFs).

Docs: docs.athenahealth.com/api/fhir-r4/{patient,diagnostic-report,observation}
"""
from __future__ import annotations

import base64

import requests

from .adapter import LabSource
from .auth import TokenProvider
from .config import Settings
from .models import LabReport, LabValue

US_CORE_LAB_CATEGORY = "http://terminology.hl7.org/CodeSystem/v2-0074|LAB"
OBS_LAB_CATEGORY = (
    "http://terminology.hl7.org/CodeSystem/observation-category|laboratory"
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
        from urllib.parse import urlparse

        entries: list[dict] = []
        bundle = self._get(resource, params)
        expected_host = urlparse(self.s.fhir_base).hostname
        while True:
            entries += [e["resource"] for e in bundle.get("entry", []) if "resource" in e]
            next_url = next(
                (l["url"] for l in bundle.get("link", []) if l.get("relation") == "next"),
                None,
            )
            if not next_url:
                return entries
            # Vendoring delta: the 'next' link is server-supplied data. Follow
            # it only back to the same FHIR host, so a poisoned bundle cannot
            # redirect the client (with its bearer token) elsewhere.
            if urlparse(next_url).hostname != expected_host:
                raise RuntimeError("FHIR paging link pointed off-host; refusing")
            resp = requests.get(
                next_url,
                headers={**self.tokens.auth_headers(), "Accept": "application/fhir+json"},
                timeout=60,
            )
            resp.raise_for_status()
            bundle = resp.json()

    # ---------- LabSource ----------
    def find_patient(self, family, given=None, birthdate=None):
        # athena requires one of: [_id], [identifier], [name],
        # [family,birthdate], [family,gender], [family,given]
        params = {"ah-practice": self.s.ah_practice}
        if given:
            params["family"] = family
            params["given"] = given
        elif birthdate:
            params["family"] = family
            params["birthdate"] = birthdate
        else:
            params["name"] = family
        out = []
        for p in self._search_all("Patient", params):
            names = p.get("name", [{}])
            out.append({
                "id": p.get("id"),
                "name": " ".join(names[0].get("given", []) + [names[0].get("family", "")]).strip(),
                "birthdate": p.get("birthDate"),
            })
        return out

    def get_lab_reports(self, patient_id, since=None):
        params = {
            "patient": patient_id,
            "category": US_CORE_LAB_CATEGORY,
            "ah-practice": self.s.ah_practice,
        }
        if since:
            params["date"] = f"ge{since}"
        try:
            reports = self._search_all("DiagnosticReport", params)
        except RuntimeError:
            # Some servers accept the bare code; retry once (see plan §7 open items).
            params["category"] = "LAB"
            reports = self._search_all("DiagnosticReport", params)

        # Pull the patient's lab Observations once, index by id for fast joining.
        obs_params = {
            "patient": patient_id,
            "category": OBS_LAB_CATEGORY,
            "ah-practice": self.s.ah_practice,
        }
        try:
            observations = self._search_all("Observation", obs_params)
        except RuntimeError:
            obs_params["category"] = "laboratory"
            observations = self._search_all("Observation", obs_params)
        obs_by_id = {o["id"]: o for o in observations}

        out = []
        for r in reports:
            report = LabReport(
                report_id=r.get("id", ""),
                name=(r.get("code", {}).get("text")
                      or _first_coding_display(r.get("code", {}))),
                loinc=_first_loinc(r.get("code", {})),
                status=r.get("status"),
                collected_at=r.get("effectiveDateTime")
                or (r.get("effectivePeriod") or {}).get("start"),
                resulted_at=r.get("issued"),
                performing_lab=_first_performer(r),
                has_document=any(
                    (pf.get("url") or "").split("/Binary/")[-1]
                    for pf in r.get("presentedForm", [])
                ) or bool(r.get("presentedForm")),
                raw=r,
            )
            for ref in r.get("result", []):
                obs_id = (ref.get("reference") or "").split("/")[-1]
                obs = obs_by_id.get(obs_id) or (self._get(f"Observation/{obs_id}") if obs_id else None)
                if obs:
                    report.values.append(_observation_to_value(obs))
            out.append(report)

        # Standalone lab observations not tied to a fetched report are still
        # valuable for trending; surface them as a synthetic report.
        seen = {v.source_id for rep in out for v in rep.values}
        orphans = [_observation_to_value(o) for o in observations if o["id"] not in seen]
        if orphans:
            out.append(LabReport(report_id="unattached-observations",
                                 name="Lab observations without a report",
                                 values=orphans))
        return out

    def get_report_document(self, report):
        for pf in (report.raw or {}).get("presentedForm", []):
            url = pf.get("url") or ""
            if "/Binary/" in url:
                binary = self._get(f"Binary/{url.split('/Binary/')[-1]}")
                if binary.get("data"):
                    return base64.b64decode(binary["data"])
            if pf.get("data"):
                return base64.b64decode(pf["data"])
        return None


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


def _first_performer(report: dict) -> str | None:
    for p in report.get("performer", []):
        if p.get("display"):
            return p["display"]
    return None


def _observation_to_value(obs: dict) -> LabValue:
    vq = obs.get("valueQuantity") or {}
    value = (
        str(vq.get("value")) if vq.get("value") is not None
        else obs.get("valueString")
        or (obs.get("valueCodeableConcept") or {}).get("text")
    )
    rr = obs.get("referenceRange", [{}])[0]
    ref = rr.get("text")
    if not ref and (rr.get("low") or rr.get("high")):
        lo, hi = (rr.get("low") or {}).get("value"), (rr.get("high") or {}).get("value")
        ref = f"{lo if lo is not None else ''}-{hi if hi is not None else ''}"
    interp = obs.get("interpretation", [{}])[0]
    return LabValue(
        name=(obs.get("code", {}).get("text") or _first_coding_display(obs.get("code", {})) or "?"),
        loinc=_first_loinc(obs.get("code", {})),
        value=value,
        unit=vq.get("unit") or vq.get("code"),
        reference_range=ref,
        abnormal_flag=interp.get("text") or _first_coding_display(interp),
        status=obs.get("status"),
        observed_at=obs.get("effectiveDateTime") or obs.get("issued"),
        source_id=obs.get("id"),
    )
