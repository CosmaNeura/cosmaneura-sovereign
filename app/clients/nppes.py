"""
NPPES NPI Registry client (CMS, public, free, no API key).

Endpoint verified from the CMS NPPES Read API v2.1:
  GET {base}/?version=2.1&state=OH&taxonomy_description=Dermatology&limit=20

This is the *broad* provider source: it covers every US provider and is used
for geography + specialty matching, especially when a patient is open to
non-Catholic providers. The "faithful Catholic" attribute does NOT exist in
NPPES — that comes from the partner directory (MyCatholicDoctor + partners).
"""
from __future__ import annotations

from typing import Optional

import httpx

from app.compliance.netsec import safe_error_message
from app.config import Settings, get_settings


class NPPESClient:
    def __init__(self, settings: Optional[Settings] = None):
        self.s = settings or get_settings()

    async def search(
        self,
        *,
        state: Optional[str] = None,
        city: Optional[str] = None,
        postal_code: Optional[str] = None,
        taxonomy_description: Optional[str] = None,  # NUCC specialty, supports trailing *
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        enumeration_type: Optional[str] = None,       # "NPI-1" individual / "NPI-2" org
        limit: int = 20,
    ) -> list[dict]:
        params = {"version": "2.1", "limit": min(limit, 200)}
        for k, v in {
            "state": state, "city": city, "postal_code": postal_code,
            "taxonomy_description": taxonomy_description, "first_name": first_name,
            "last_name": last_name, "enumeration_type": enumeration_type,
        }.items():
            if v:
                params[k] = v

        try:
            async with httpx.AsyncClient(timeout=self.s.request_timeout_seconds) as client:
                resp = await client.get(f"{self.s.nppes_base_url}/", params=params)
                resp.raise_for_status()
                results = resp.json().get("results", [])
        except httpx.HTTPError as exc:
            raise RuntimeError(safe_error_message(exc, context="NPPES")) from None
        return [_summarize(r) for r in results]


def _summarize(record: dict) -> dict:
    basic = record.get("basic", {})
    taxonomies = record.get("taxonomies", [])
    primary = next((t for t in taxonomies if t.get("primary")), taxonomies[0] if taxonomies else {})
    location = next(
        (a for a in record.get("addresses", []) if a.get("address_purpose") == "LOCATION"),
        {},
    )
    name = basic.get("organization_name") or " ".join(
        filter(None, [basic.get("first_name"), basic.get("last_name")])
    )
    return {
        "npi": record.get("number"),
        "name": name,
        "credential": basic.get("credential"),
        "specialty": primary.get("desc"),
        "city": location.get("city"),
        "state": location.get("state"),
        "postal_code": location.get("postal_code"),
        "phone": location.get("telephone_number"),
        "source": "NPPES",
        "is_catholic": None,  # unknown from NPPES; resolved by partner directory
    }
