"""
PubMed / PMC client (NCBI E-utilities — NIH/NLM, public, free).

Endpoints per the E-utilities docs (api key optional; raises rate limits):
  GET {base}/esearch.fcgi?db=pubmed&term=...&retmode=json&retmax=N
  GET {base}/esummary.fcgi?db=pubmed&id=1,2,3&retmode=json

Powers Agent 4 (ethics consult): grounds consult prep in published,
peer-reviewed literature — including the Catholic bioethics corpus. The
Linacre Quarterly (the Catholic Medical Association's peer-reviewed journal,
indexed as "Linacre Q") has content reachable through this same free API, so
the "only if it is published" corpus is available with no partnership.

PHI safety: E-utilities is a public, non-BAA API. Queries are TOPIC-ONLY.
Callers must pass de-identified terms; this client additionally carries the
same hard `assert_no_phi` tripwire as the Magisterium client, so an
identifier-bearing query fails loudly instead of leaking.
"""
from __future__ import annotations

from typing import Optional

import httpx

from app.compliance.netsec import UpstreamCallError, safe_error_message
from app.compliance.phi import assert_no_phi
from app.config import Settings, get_settings

# Journal filter for the Catholic bioethics corpus. PubMed journal abbreviation
# for The Linacre Quarterly; extend as further CMA/bioethics journals are added.
LINACRE_JOURNAL = "Linacre Q"

_MOCK_RESULTS = [
    {
        "pmid": "34000001",
        "title": "[MOCK] Ethical and Religious Directives in clinical practice: "
                 "a review for the bedside clinician",
        "journal": "The Linacre Quarterly",
        "year": "2024",
        "url": "https://pubmed.ncbi.nlm.nih.gov/34000001/",
        "source": "PubMed",
    },
    {
        "pmid": "34000002",
        "title": "[MOCK] Clinical evidence summary relevant to the consult topic",
        "journal": "JAMA",
        "year": "2025",
        "url": "https://pubmed.ncbi.nlm.nih.gov/34000002/",
        "source": "PubMed",
    },
]


class PubMedClient:
    def __init__(self, settings: Optional[Settings] = None):
        self.s = settings or get_settings()

    async def search(
        self,
        term: str,
        *,
        journal: Optional[str] = None,
        max_results: int = 5,
    ) -> list[dict]:
        """Search PubMed by topic; returns compact summaries with URLs."""
        # Tripwire runs FIRST, even in mock mode, so the invariant is always
        # enforced and testable — mirrors the Magisterium client.
        assert_no_phi(term)

        if self.s.mock_mode:
            hits = list(_MOCK_RESULTS)
            if journal:
                hits = [h for h in hits if journal.lower().split()[0]
                        in h["journal"].lower()]
            return hits[:max_results]

        query = f'{term} AND "{journal}"[jour]' if journal else term
        params: dict[str, str | int] = {
            "db": "pubmed", "term": query, "retmode": "json",
            "retmax": min(max_results, 50), "sort": "relevance",
        }
        if self.s.pubmed_api_key:
            params["api_key"] = self.s.pubmed_api_key

        try:
            async with httpx.AsyncClient(timeout=self.s.request_timeout_seconds) as client:
                resp = await client.get(f"{self.s.pubmed_base_url}/esearch.fcgi",
                                        params=params)
                resp.raise_for_status()
                ids = resp.json().get("esearchresult", {}).get("idlist", [])
                if not ids:
                    return []
                sparams: dict[str, str] = {"db": "pubmed", "id": ",".join(ids),
                                           "retmode": "json"}
                if self.s.pubmed_api_key:
                    sparams["api_key"] = self.s.pubmed_api_key
                sresp = await client.get(f"{self.s.pubmed_base_url}/esummary.fcgi",
                                         params=sparams)
                sresp.raise_for_status()
                summaries = sresp.json().get("result", {})
        except httpx.HTTPError as exc:
            raise UpstreamCallError(safe_error_message(exc, context="PubMed")) from None

        return [_summarize(pmid, summaries.get(pmid, {})) for pmid in ids
                if pmid in summaries]


def _summarize(pmid: str, doc: dict) -> dict:
    return {
        "pmid": pmid,
        "title": doc.get("title"),
        "journal": doc.get("fulljournalname") or doc.get("source"),
        "year": (doc.get("pubdate") or "").split(" ")[0],
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "source": "PubMed",
    }
