"""
Agent 1 — Referral / Doctor-Matching ("the Steward").

Brief: take patient inputs + metadata to identify the best doctor when they
say they want to see one, across Catholic/not-Catholic, geography, specialty,
and language. Engages MyCatholicDoctor and any resource partner that wants
routed patient traffic.

Source strategy:
  * Partner Directory  -> the curated faithful-Catholic + partner set (this is
    the only place the "is_catholic" attribute and booking links exist).
  * NPPES NPI Registry -> broad geography/specialty coverage, used when the
    patient is open to non-Catholic providers or no Catholic match is found.

The LLM does (a) slot extraction from free text and (b) final ranking/narration
with a clear booking handoff. No PHI is required for matching; only coarse
location/preference, so this agent is safe in both personas.
"""
from __future__ import annotations

import re
from typing import Optional

from app.agents.base import BaseAgent
from app.clients.nppes import NPPESClient
from app.clients.partner_directory import PartnerDirectoryClient
from app.compliance.phi import deidentify
from app.config import ComplianceProfile
from app.schemas import AgentResult, ChatRequest, Citation

_SLOT_SYSTEM = (
    "You extract structured search slots for a Catholic doctor-matching service. "
    "Given the user's request and metadata, output the fields you can infer."
)

# Cap how many providers reach the narration LLM. The telehealth-first directory
# can match hundreds of providers for any state, and dumping them all into the
# prompt both wastes tokens (and trips provider rate limits) and gives the model
# too much to rank. We rank locally first, then narrate on a focused shortlist.
_NARRATION_LIMIT = 12

# Map lay terms to the clinical words that appear in provider specialty strings,
# so "something with my skin" still ranks the dermatologist first. Each value is
# expanded into the request before root-matching against the provider specialty.
_LAY_TO_CLINICAL = {
    "skin": "dermatology", "rash": "dermatology", "acne": "dermatology",
    "mole": "dermatology", "primary": "family internal medicine",
    "mental": "psychiatry counseling psychotherapy therapy",
    "anxiety": "counseling psychotherapy therapy",
    "depression": "counseling psychotherapy psychiatry",
    "therapist": "psychotherapy counseling therapy", "therapy": "psychotherapy counseling",
    "counsel": "counseling psychotherapy", "kid": "pediatric", "kids": "pediatric",
    "child": "pediatric", "children": "pediatric", "baby": "pediatric",
    "fertility": "fertilitycare obstetrics gynecology reproductive",
    "pregnan": "obstetrics gynecology", "ob": "obstetrics gynecology",
    "diet": "dietitian nutrition", "nutrition": "dietitian nutrition",
    "heart": "cardiology", "hormone": "endocrinology",
}


# Words too generic to signal a specialty match (they appear in many unrelated
# specialty strings, e.g. "care" in "FertilityCare").
_GENERIC = {"care", "medicine", "medical", "health", "clinical", "certified",
            "licensed", "practitioner", "general", "model", "method", "board",
            "consultant", "specialist", "physician", "nurse", "associate"}


def _specialty_relevant(requested: Optional[str], provider_specialty: Optional[str]) -> bool:
    """Does the provider's specialty plausibly match what was asked for?"""
    if not requested:
        return False
    req = requested.lower()
    prov = (provider_specialty or "").lower()
    expanded = req
    for lay, clinical in _LAY_TO_CLINICAL.items():
        if lay in req:
            expanded += " " + clinical
    # Root-match (4+ char tokens, compared on a 5-char stem) so e.g.
    # "dermatology" matches "Dermatologist" and "family" matches "Family Medicine".
    for tok in re.findall(r"[a-z]{4,}", expanded):
        if tok in _GENERIC:
            continue
        if tok[:5] in prov:
            return True
    return False


class ReferralAgent(BaseAgent):
    name = "referral_agent"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.directory = PartnerDirectoryClient(self.s)
        self.nppes = NPPESClient(self.s)

    async def run(self, request: ChatRequest, profile: ComplianceProfile) -> AgentResult:
        # Matching never needs PHI; scrub defensively before slot extraction.
        clean = deidentify(request.message).text

        slots = await self.llm.classify_json(
            system=_SLOT_SYSTEM,
            prompt=(
                f"Request: {clean}\nMetadata: {request.metadata}\n\n"
                "Fields: state (2-letter US), specialty (NUCC-style description), "
                "catholic_only (bool, default true for this service), language, "
                "telehealth_ok (bool, default true)."
            ),
        )
        state = (slots.get("state") or request.metadata.get("state") or "").upper() or None
        specialty = slots.get("specialty") or request.metadata.get("specialty")
        catholic_only = bool(slots.get("catholic_only", True))
        language = slots.get("language") or request.metadata.get("language")

        # 1) Curated Catholic + partner providers (with booking handoff).
        catholic_matches = await self.directory.search(
            state=state, specialty=specialty, catholic_only=True,
            telehealth_ok=bool(slots.get("telehealth_ok", True)), language=language,
        )

        # 2) Broaden via NPPES if the patient is open to non-Catholic providers
        #    or no faithful match surfaced.
        broad_matches: list[dict] = []
        if (not catholic_only or not catholic_matches) and state:
            broad_matches = await self.nppes.search(
                state=state, taxonomy_description=(specialty + "*") if specialty else None,
                limit=10,
            )

        combined = self._rank(catholic_matches + broad_matches, state, specialty)[:_NARRATION_LIMIT]
        narration = await self._narrate(clean, combined, catholic_only)

        return AgentResult(
            agent=self.name,
            content=narration,
            citations=[
                Citation(source="MyCatholicDoctor", url="https://mycatholicdoctor.com/"),
                Citation(source="NPPES NPI Registry",
                         url="https://npiregistry.cms.hhs.gov/"),
            ],
            data={"matches": combined, "slots": {"state": state, "specialty": specialty,
                                                 "catholic_only": catholic_only}},
        )

    @staticmethod
    def _rank(matches: list[dict], state: Optional[str],
              specialty: Optional[str] = None) -> list[dict]:
        """
        Rank candidates so the result is actually relevant, not arbitrary:
          1. available in the requested state (a provider not licensed there
             can't actually see the patient — so availability gates first),
          2. specialty relevance (a dermatology request surfaces dermatologists),
          3. telehealth (the network is telehealth-first),
          4. name (stable tiebreak).
        When no state is given, (1) is neutral and specialty leads.
        """
        st = state.upper() if state else None

        def key(p: dict):
            in_state = bool(st and st in set(p.get("licensed_states", [])))
            spec_hit = _specialty_relevant(specialty, p.get("specialty"))
            return (0 if in_state else 1, 0 if spec_hit else 1,
                    0 if p.get("telehealth") else 1, p.get("name", ""))

        return sorted(matches, key=key)

    async def _narrate(self, request: str, matches: list[dict], catholic_only: bool) -> str:
        if not matches:
            return ("I couldn't find a matching provider with the details given. "
                    "Could you share your U.S. state and the kind of care you're "
                    "looking for? Catholic telehealth providers can often see you "
                    "even if they aren't physically in your state.")
        return await self.llm.complete(
            system=(
                "You present doctor matches for a Catholic healthcare service warmly "
                "and concisely. Prioritize faithful Catholic providers. For each match, "
                "give name, specialty, location/telehealth, and the booking link. End "
                "by offering to refine the search. Never claim a provider endorses any "
                "particular treatment."
            ),
            prompt=f"User asked: {request}\nMatches (JSON): {matches}",
            max_tokens=700,
        )
