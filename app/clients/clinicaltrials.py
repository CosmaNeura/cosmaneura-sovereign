"""
ClinicalTrials.gov API v2 client (NIH/NLM, public, free, no API key).

Endpoint verified from the v2 docs (the v1 API was retired June 2024):
  GET {base}/studies?query.cond=...&query.spons=...&filter.overallStatus=RECRUITING
      &filter.geo=distance(LAT,LON,50mi)&pageSize=20&format=json

Powers Agent 2: search recruiting trials by condition, sponsor/company, phase,
and geography — exactly the "advanced trials, specific companies and people"
brief. Returns sponsor + contact data the agent can speak to (never as a
recommendation; always with experimental-status framing).
"""
from __future__ import annotations

from typing import Optional

import httpx

from app.compliance.netsec import safe_error_message
from app.config import Settings, get_settings

VALID_STATUSES = {
    "RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION",
    "ACTIVE_NOT_RECRUITING", "COMPLETED", "TERMINATED", "WITHDRAWN", "SUSPENDED",
}


class ClinicalTrialsClient:
    def __init__(self, settings: Optional[Settings] = None):
        self.s = settings or get_settings()

    async def search(
        self,
        *,
        condition: Optional[str] = None,
        intervention: Optional[str] = None,
        sponsor: Optional[str] = None,
        status: Optional[list[str]] = None,
        geo: Optional[tuple[float, float, int]] = None,  # (lat, lon, miles)
        page_size: int = 20,
    ) -> list[dict]:
        params: dict[str, str | int] = {"format": "json", "pageSize": min(page_size, 1000)}
        if condition:
            params["query.cond"] = condition
        if intervention:
            params["query.intr"] = intervention
        if sponsor:
            params["query.spons"] = sponsor
        if status:
            bad = set(status) - VALID_STATUSES
            if bad:
                raise ValueError(f"Invalid status values: {bad}")
            params["filter.overallStatus"] = ",".join(status)
        if geo:
            lat, lon, miles = geo
            params["filter.geo"] = f"distance({lat},{lon},{miles}mi)"

        try:
            async with httpx.AsyncClient(timeout=self.s.request_timeout_seconds) as client:
                resp = await client.get(f"{self.s.clinicaltrials_base_url}/studies", params=params)
                resp.raise_for_status()
                studies = resp.json().get("studies", [])
        except httpx.HTTPError as exc:
            raise RuntimeError(safe_error_message(exc, context="ClinicalTrials")) from None
        return [_summarize(s) for s in studies]


def _summarize(study: dict) -> dict:
    proto = study.get("protocolSection", {})
    ident = proto.get("identificationModule", {})
    status = proto.get("statusModule", {})
    design = proto.get("designModule", {})
    sponsor = proto.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {})
    conditions = proto.get("conditionsModule", {}).get("conditions", [])
    return {
        "nct_id": ident.get("nctId"),
        "title": ident.get("briefTitle"),
        "status": status.get("overallStatus"),
        "phase": ", ".join(design.get("phases", []) or []),
        "lead_sponsor": sponsor.get("name"),
        "conditions": conditions,
        "url": f"https://clinicaltrials.gov/study/{ident.get('nctId')}",
        "source": "ClinicalTrials.gov",
    }
