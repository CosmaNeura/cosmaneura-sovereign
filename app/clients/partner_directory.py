"""
Partner Directory adapter — the curated, monetizable provider layer.

This is the source of truth for the "faithful Catholic" attribute (which does
NOT exist in NPPES) and the place where *any resource partner that wants
patient traffic* registers their providers and booking links.

Two modes:
  * If `partner_directory_url` is set, GET it (expects the same JSON shape as
    the fixture). This is where a future MyCatholicDoctor feed/API plugs in.
  * Otherwise, read the local fixture `partners_fixture.json`.

Each entry is a referral target with a `booking_url` (the traffic handoff) and
a `partner` label so referrals can be attributed/metered per partner.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import httpx

from app.compliance.netsec import assert_safe_outbound_url, safe_error_message
from app.config import Settings, get_settings

_FIXTURE = Path(__file__).parent / "partners_fixture.json"


class PartnerDirectoryClient:
    def __init__(self, settings: Optional[Settings] = None):
        self.s = settings or get_settings()

    async def _load(self) -> list[dict]:
        if self.s.partner_directory_url:
            assert_safe_outbound_url(self.s.partner_directory_url)
            headers = {}
            if self.s.partner_directory_api_key:
                headers["Authorization"] = f"Bearer {self.s.partner_directory_api_key}"
            try:
                async with httpx.AsyncClient(timeout=self.s.request_timeout_seconds) as client:
                    resp = await client.get(self.s.partner_directory_url, headers=headers)
                    resp.raise_for_status()
                    return resp.json().get("providers", [])
            except httpx.HTTPError as exc:
                raise RuntimeError(safe_error_message(exc, context="PartnerDirectory")) from None
        # Local scraped feed (tools/scrape_mycatholicdoctor.py) if configured,
        # otherwise the bundled sample fixture. If the configured path doesn't
        # exist yet (e.g. the shared volume before the first scrape job runs),
        # fall back to the fixture rather than failing the request.
        path = _FIXTURE
        if self.s.partner_directory_path:
            candidate = Path(self.s.partner_directory_path)
            path = candidate if candidate.exists() else _FIXTURE
        return json.loads(path.read_text()).get("providers", [])

    async def search(
        self,
        *,
        state: Optional[str] = None,
        specialty: Optional[str] = None,
        catholic_only: bool = True,
        telehealth_ok: bool = True,
        language: Optional[str] = None,
    ) -> list[dict]:
        providers = await self._load()
        out = []
        for p in providers:
            # Sanitize the traffic-handoff link: a compromised or sloppy partner
            # feed must not be able to inject javascript:/data:/http: links that
            # flow into a frontend. https only; anything else is dropped.
            url = p.get("booking_url")
            if url is not None and not (isinstance(url, str) and url.startswith("https://")):
                p = {**p, "booking_url": None}
            if catholic_only and not p.get("is_catholic"):
                continue
            # Telehealth providers match by licensure state; in-person by location.
            states = set(p.get("licensed_states", []))
            if state and states and state.upper() not in states:
                if not (telehealth_ok and p.get("telehealth")):
                    continue
            if specialty and specialty.lower() not in (p.get("specialty", "").lower()):
                # soft match only; the ranking LLM can reconcile near-misses
                pass
            if language and language.lower() not in [
                item.lower() for item in p.get("languages", [])
            ]:
                continue
            out.append(p)
        return out
