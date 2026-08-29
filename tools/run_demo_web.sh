#!/usr/bin/env bash
#
# Run either the offline named-provider walkthrough or the existing anonymous
# public demo. Named-provider mode does not source .env.demo.local or call Azure.
#
#   ./tools/run_demo_web.sh --named-provider
#   ./tools/run_demo_web.sh                  # existing public/live demo
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "${1:-}" == "--named-provider" || "${1:-}" == "--named-beta" ]]; then
  test -x .venv/bin/python || {
    echo "Missing .venv. Run: make install" >&2
    exit 1
  }
  DEMO_PORT="${SOVEREIGN_DEMO_PORT:-8000}"
  DEV_SECRET="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))')"
  PROVIDER_TOKEN="$(AUTH_DEV_SECRET="$DEV_SECRET" PYTHONPATH=. \
    .venv/bin/python tools/mint_dev_token.py --role provider \
      --subject local-named-provider --ttl 28800)"

  if command -v pbcopy >/dev/null; then
    printf %s "$PROVIDER_TOKEN" | pbcopy
    echo "→ short-lived provider fixture token copied to the clipboard"
  else
    echo "→ paste this short-lived local fixture token into the walkthrough:"
    echo "$PROVIDER_TOKEN"
  fi

  export MOCK_MODE=true MOUNT_PROFILE=provider_assistant \
    MOUNT_TENANT_ID=tenant_local_named_beta AUTH_MODE=dev_hs256 \
    AUTH_DEV_SECRET="$DEV_SECRET" AUTH_ROLE_CLAIM=roles AUTH_PROVIDER_ROLE=provider \
    ALLOW_PROVIDER_PERSONA=false BAA_ATTESTED=false DEMO_UI_ENABLED=true \
    PLATFORM_AGENT_API_ENABLED=false FEATURE_ATHENA_LABS=false \
    LABS_ALLOW_PATIENT_SEARCH=false MAGISTERIUM_MCP_ENABLED=false \
    DEID_PROVIDER=regex CORS_ALLOW_ORIGINS='[]' PYTHONPATH=.
  export MAGISTERIUM_API_KEY='' ANTHROPIC_API_KEY='' AZURE_OPENAI_ENDPOINT='' \
    AZURE_OPENAI_API_KEY='' PARTNER_DIRECTORY_URL='' PARTNER_DIRECTORY_API_KEY='' \
    PARTNER_DIRECTORY_PATH='' ATHENA_CLIENT_ID='' ATHENA_CLIENT_SECRET='' \
    SOVEREIGN_RECORD_GRANT_SECRET='' PLATFORM_AGENT_API_BASE_URL='' \
    PLATFORM_AGENT_API_SCOPE='' PLATFORM_WORKLOAD_CLIENT_ID='' \
    AZURE_DEID_ENDPOINT='' AZURE_DEID_TENANT_ID='' AZURE_DEID_CLIENT_ID='' \
    AZURE_DEID_CLIENT_SECRET='' \
    SOVEREIGN_DEPLOY_ENABLED=false
  unset OIDC_ISSUER OIDC_AUDIENCE OIDC_JWKS_URL

  URL="http://127.0.0.1:${DEMO_PORT}/demo/dashboard"
  echo "→ starting loopback-only mock provider host at $URL"
  echo "→ paste the copied token, then use a synthetic guided path"
  if [[ "${SOVEREIGN_DEMO_NO_OPEN:-false}" != "true" ]]; then
    ( sleep 2; (command -v open >/dev/null && open "$URL") || true ) &
  fi
  exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$DEMO_PORT"
fi

if [[ $# -gt 0 ]]; then
  echo "Usage: $0 [--named-provider]" >&2
  exit 2
fi

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
