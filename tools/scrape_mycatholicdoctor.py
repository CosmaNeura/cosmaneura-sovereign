"""
Scrape the MyCatholicDoctor public clinician directory into the Partner
Directory schema (app/clients/partners_fixture.json shape).

MyCatholicDoctor has NO public API, but publishes a static, per-state clinician
listing at:  https://mycatholicdoctor.com/providers/all-providers/<state>/
Each page lists ~60 clinicians as `div.mcd_grid_bio` blocks (name in <strong>,
a specialty/role line, a description, a "*Languages:" line, and About /
Make Appointment links). Because the network is telehealth-first, a clinician
appears on every state where they're licensed — so we dedupe by profile slug and
AGGREGATE `licensed_states` across pages.

Output matches the integration contract the ReferralAgent already consumes, so
no agent code changes: each provider has a booking_url (the traffic handoff) and
partner="MyCatholicDoctor" (for attribution/metering).

Usage:
    python tools/scrape_mycatholicdoctor.py --states CO,CA,NH --out app/clients/providers.generated.json
    python tools/scrape_mycatholicdoctor.py            # all states -> default out
Be a good citizen: this throttles between requests. Respect the site's ToS and
your data-sharing agreement with MyCatholicDoctor before production use.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time

import httpx
from bs4 import BeautifulSoup

BASE = "https://mycatholicdoctor.com"
LIST_URL = BASE + "/providers/all-providers/{slug}/"

# US states + DC -> URL slug used by the site.
STATES: dict[str, str] = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas",
    "CA": "california", "CO": "colorado", "CT": "connecticut", "DE": "delaware",
    "DC": "district-of-columbia", "FL": "florida", "GA": "georgia", "HI": "hawaii",
    "ID": "idaho", "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas",
    "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi",
    "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new-hampshire", "NJ": "new-jersey", "NM": "new-mexico", "NY": "new-york",
    "NC": "north-carolina", "ND": "north-dakota", "OH": "ohio", "OK": "oklahoma",
    "OR": "oregon", "PA": "pennsylvania", "RI": "rhode-island", "SC": "south-carolina",
    "SD": "south-dakota", "TN": "tennessee", "TX": "texas", "UT": "utah",
    "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "west-virginia",
    "WI": "wisconsin", "WY": "wyoming",
}

UA = "Mozilla/5.0 (CosmaNeura partner-directory integration; +https://cosmaneura.com)"


def _abs(href: str) -> str:
    return href if href.startswith("http") else BASE + href


def parse_state(html: str, state: str, out: dict) -> int:
    soup = BeautifulSoup(html, "lxml")
    found = 0
    # Each clinician is one `div.mcd_grid_row`: a `.mcd_grid_bio` column (name +
    # text) plus a sibling column holding the About / Make Appointment links.
    for row in soup.select("div.mcd_grid_row"):
        bio = row.select_one(".mcd_grid_bio")
        if not bio:
            continue
        strong = bio.find("strong")
        prof = row.find("a", href=re.compile(r"/resources/doctors/"))
        if not strong or not prof:
            continue
        m = re.search(r"/resources/doctors/([^/]+)/", prof["href"])
        if not m:
            continue
        slug = m.group(1)
        name = strong.get_text(strip=True)

        appt = row.find("a", href=re.compile(r"/make-appointment/"))
        booking_url = _abs(appt["href"]) if appt else _abs(prof["href"])

        # text lines minus the link labels
        lines = [l.strip() for l in bio.get_text("\n").split("\n") if l.strip()]
        lines = [l for l in lines if l.lower() not in ("about", "make appointment")]
        rest = [l for l in lines if l != name]
        specialty, languages, desc = None, [], []
        for l in rest:
            low = l.lower()
            if low.startswith("*languages") or low.startswith("languages"):
                langs = l.split(":", 1)[1] if ":" in l else ""
                languages = [x.strip() for x in re.split(r"[/,&]|\band\b", langs) if x.strip()]
            elif specialty is None:
                specialty = l
            else:
                desc.append(l)

        if slug in out:
            st = out[slug]["licensed_states"]
            if state not in st:
                st.append(state)
            found += 1
            continue

        credential = name.split(",", 1)[1].strip() if "," in name else None
        out[slug] = {
            "id": f"mcd-{slug}",
            "name": name,
            "credential": credential,
            # keep the role/department title as specialty; trim the "│Chair…" part
            "specialty": (specialty.split("│")[0].strip() if specialty else None),
            "is_catholic": True,
            "telehealth": True,  # MCD is telehealth-first (licensed across states)
            "licensed_states": [state],
            "languages": languages or ["English"],
            "partner": "MyCatholicDoctor",
            "booking_url": booking_url if booking_url.startswith("https://") else None,
            "profile_url": _abs(prof["href"]),
            "notes": " ".join(desc)[:400] or None,
        }
        found += 1
    return found


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--states", default="", help="comma list of 2-letter states; default all")
    ap.add_argument("--out", default="app/clients/providers.generated.json")
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between requests")
    args = ap.parse_args()

    targets = ([s.strip().upper() for s in args.states.split(",") if s.strip()]
               if args.states else list(STATES))
    out: dict = {}
    with httpx.Client(timeout=30, headers={"User-Agent": UA}, follow_redirects=True) as c:
        for i, st in enumerate(targets):
            slug = STATES.get(st)
            if not slug:
                print(f"  ! unknown state {st}, skipping", file=sys.stderr)
                continue
            try:
                r = c.get(LIST_URL.format(slug=slug))
                if r.status_code == 404:
                    print(f"  - {st}: 404 (no page)", file=sys.stderr)
                    continue
                r.raise_for_status()
                n = parse_state(r.text, st, out)
                print(f"  - {st}: {n} entries (total unique: {len(out)})", file=sys.stderr)
            except httpx.HTTPError as e:
                print(f"  ! {st}: {e.__class__.__name__}", file=sys.stderr)
            if i < len(targets) - 1:
                time.sleep(args.delay)

    providers = sorted(out.values(), key=lambda p: p["name"])
    payload = {
        "_generated_by": "tools/scrape_mycatholicdoctor.py",
        "_source": "https://mycatholicdoctor.com/providers/all-providers/<state>/",
        "providers": providers,
    }
    with open(args.out, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\nWrote {len(providers)} unique providers to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
