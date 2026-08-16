#!/usr/bin/env bash
#
# One command to run the anonymous public mount locally against real Azure
# OpenAI and the scraped MyCatholicDoctor directory. Requires: `az login`, the
# .venv, and a scraped data file (tools/scrape_mycatholicdoctor.py).
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

# This launcher is public-only. Remove stale provider/EHR values that may be
# present in an older .env.demo.local; public startup intentionally rejects
# them instead of relying on a runtime feature gate.
unset MOUNT_SURFACE MOUNT_AUTH_TIER MOUNT_CAPABILITIES MOUNT_TENANT_ID
unset AUTH_DEV_SECRET OIDC_ISSUER OIDC_AUDIENCE OIDC_JWKS_URL
unset ATHENA_CLIENT_ID ATHENA_CLIENT_SECRET
unset AZURE_DEID_ENDPOINT AZURE_DEID_TENANT_ID
unset AZURE_DEID_CLIENT_ID AZURE_DEID_CLIENT_SECRET

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
  MOUNT_PROFILE=public \
  AUTH_MODE=disabled \
  ALLOW_PROVIDER_PERSONA=false \
  BAA_ATTESTED=false \
  FEATURE_ATHENA_LABS=false \
  LABS_ALLOW_PATIENT_SEARCH=false \
  DEID_PROVIDER=regex \
  PUBLIC_CHAT_TRUST_FORWARDED_FOR=false \
  DEMO_UI_ENABLED=true \
  PYTHONPATH=.

URL="http://localhost:8000/demo"
echo "→ starting public mount…  opening $URL"
( sleep 2; (command -v open >/dev/null && open "$URL") || true ) &
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
