#!/usr/bin/env bash
#
# One command to run the live chat demo locally against real Azure OpenAI and
# the scraped MyCatholicDoctor directory. Requires: `az login`, the .venv
# (make install), and a scraped data file (tools/scrape_mycatholicdoctor.py).
#
#   ./tools/run_demo_web.sh
# then open http://localhost:8000/demo
set -euo pipefail
cd "$(dirname "$0")/.."

# Load local demo secrets (gitignored). Holds MAGISTERIUM_API_KEY, etc.
if [ -f .env.demo.local ]; then
  set -a; . ./.env.demo.local; set +a
  echo "→ loaded .env.demo.local"
fi

# Azure OpenAI resource (override via env if needed).
AOAI_NAME="${AOAI_NAME:-cosmaneuraprodopenai}"
AOAI_RG="${AOAI_RG:-cosmaneura-prod}"
AOAI_ENDPOINT="${AOAI_ENDPOINT:-https://cosmaneuraprodopenai.cognitiveservices.azure.com/}"

echo "→ fetching Azure OpenAI key via az ($AOAI_NAME / $AOAI_RG)…"
KEY="$(az cognitiveservices account keys list -n "$AOAI_NAME" -g "$AOAI_RG" --query key1 -o tsv)"
[ -n "$KEY" ] || { echo "Could not get key — is 'az login' done and the resource correct?"; exit 1; }

DATA="app/clients/providers.generated.json"
[ -f "$DATA" ] || { echo "No scraped data at $DATA — run: .venv/bin/python tools/scrape_mycatholicdoctor.py"; exit 1; }

# Magisterium key (doctrinal gate). Passed through from the environment — NEVER
# hardcode a secret here. Without it, doctor-finding still works, but morally-
# weighted / doctrinal questions won't. Provide it like:
#   MAGISTERIUM_API_KEY='sk_...' ./tools/run_demo_web.sh
MAGISTERIUM_API_KEY="${MAGISTERIUM_API_KEY:-}"
if [ -z "$MAGISTERIUM_API_KEY" ]; then
  echo "⚠️  MAGISTERIUM_API_KEY not set — doctrinal questions will be disabled."
else
  echo "→ Magisterium doctrinal gate: enabled"
fi

export MOCK_MODE=false \
  LLM_PROVIDER=azure_openai \
  AZURE_OPENAI_ENDPOINT="$AOAI_ENDPOINT" \
  AZURE_OPENAI_API_KEY="$KEY" \
  AZURE_OPENAI_API_VERSION="2025-03-01-preview" \
  REASONING_MODEL="gpt-4.1-2025-04-14" \
  FAST_MODEL="gpt-4.1-mini-2025-04-14" \
  MAGISTERIUM_API_KEY="$MAGISTERIUM_API_KEY" \
  PARTNER_DIRECTORY_PATH="$DATA" \
  AUTH_MODE=disabled \
  DEMO_UI_ENABLED=true \
  PYTHONPATH=. \
  `# Clinical-grade PHI de-id (Azure Health Data Services). Uses your az-login` \
  `# identity (needs the 'DeID Realtime Data User' role on the deid service).` \
  DEID_PROVIDER=azure \
  AZURE_DEID_ENDPOINT="https://fba4aeg2gaf0hwf0.api.eus001.deid.azure.com" \
  `# --- DEMO ONLY: enable the provider/PHI persona so the UI toggle works. ---` \
  `# ALLOW_PROVIDER_PERSONA lets a body-supplied persona=provider through since` \
  `# there is no auth in the demo. BAA_ATTESTED is a LEGAL attestation in prod —` \
  `# it is set here ONLY to exercise Agent 3 locally on Azure OpenAI (BAA-eligible)` \
  `# with synthetic data. Do NOT treat this as a real BAA attestation.` \
  ALLOW_PROVIDER_PERSONA=true \
  BAA_ATTESTED=true

URL="http://localhost:8000/demo"
echo "→ starting…  opening $URL"
( sleep 2; (command -v open >/dev/null && open "$URL") || true ) &
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
