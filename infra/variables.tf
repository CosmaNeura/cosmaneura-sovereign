variable "tags" {
  description = "Tags applied to all resources"
  type        = map(string)
  default = {
    environment = "production"
    service     = "cosmaneura-sovereign"
    managed     = "terraform"
  }
}

# GitHub repo (OWNER/REPO) and branch allowed to deploy via OIDC federation.
variable "github_repository" {
  description = "GitHub repo authorized to push images / deploy, as OWNER/REPO"
  type        = string
  default     = "CosmaNeura/cosmaneura-sovereign"
}

variable "github_branch" {
  description = "Branch whose workflow runs may deploy"
  type        = string
  default     = "main"
}

# ENV vars that must stay secret. Resolved from .env.production.backend (which
# carries op:// references) and wired in as Container App secrets.
variable "sovereign_secret_vars" {
  description = "ENV vars kept as Container App secrets"
  type        = set(string)
  default = [
    "MAGISTERIUM_API_KEY",
    # LLM inference. Azure OpenAI is the configured path (BAA-covered under the
    # Azure BAA, reuses the main app's resource). For Anthropic instead, swap
    # this for "ANTHROPIC_API_KEY".
    "AZURE_OPENAI_API_KEY",
    # Add "MAGISTERIUM_MCP_TOKEN" here only when the optional inline doctrinal
    # MCP layer is enabled (MAGISTERIUM_MCP_ENABLED=true) — otherwise it would
    # force an unused, empty Container App secret.
  ]
}

# Non-secret container configuration. Also sourced from the same env export so
# there is a single source of truth per environment.
variable "sovereign_public_vars" {
  description = "ENV vars that can be public"
  type        = set(string)
  default = [
    "MOCK_MODE",
    "SOVEREIGN_MODE",
    "MOUNT_TENANT_ID",
    # auth (bound to Clerk via OIDC — see ../DEPLOY_AZURE.md §2)
    "AUTH_MODE", "OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL",
    "OIDC_ALGORITHMS", "AUTH_ROLE_CLAIM", "AUTH_PROVIDER_ROLE",
    # compliance gates
    "ALLOW_PROVIDER_PERSONA", "BAA_ATTESTED",
    # clinical de-id (keyless via managed identity — see deid.tf)
    "DEID_PROVIDER", "AZURE_DEID_ENDPOINT",
    # reasoning LLM (Azure OpenAI: endpoint is a URL, not a secret)
    "LLM_PROVIDER", "REASONING_MODEL", "FAST_MODEL",
    "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_VERSION",
    # doctrinal authority + public data sources
    "MAGISTERIUM_BASE_URL", "MAGISTERIUM_MODEL",
    "MAGISTERIUM_MCP_ENABLED", "ANTHROPIC_MCP_BETA",
    "NPPES_BASE_URL", "CLINICALTRIALS_BASE_URL",
    # audit + web
    "AUDIT_SINK", "AUDIT_PATH", "CORS_ALLOW_ORIGINS",
  ]
}

# The public app uses a separate environment export and a deliberately smaller
# allowlist. In particular, no EHR, BAA, provider-auth, or de-id setting can be
# injected by these dynamic blocks.
variable "public_secret_vars" {
  description = "Secret ENV vars available to the anonymous public app"
  type        = set(string)
  default = [
    "MAGISTERIUM_API_KEY",
    "AZURE_OPENAI_API_KEY",
  ]
}

variable "public_public_vars" {
  description = "Non-secret ENV vars available to the anonymous public app"
  type        = set(string)
  default = [
    "MOCK_MODE", "SOVEREIGN_MODE",
    "LLM_PROVIDER", "REASONING_MODEL", "FAST_MODEL",
    "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_VERSION",
    "MAGISTERIUM_BASE_URL", "MAGISTERIUM_MODEL",
    "MAGISTERIUM_MCP_ENABLED", "ANTHROPIC_MCP_BETA",
    "NPPES_BASE_URL", "CLINICALTRIALS_BASE_URL",
    "AUDIT_SINK", "AUDIT_PATH", "CORS_ALLOW_ORIGINS",
    "PUBLIC_CHAT_RATE_LIMIT_REQUESTS",
    "PUBLIC_CHAT_RATE_LIMIT_WINDOW_SECONDS",
    "PUBLIC_CHAT_RATE_LIMIT_MAX_CLIENTS",
  ]
}

# Start commands by process type (mirrors cosmaneura/infra). Reads ../Procfile.
data "external" "procfile" {
  program     = ["yq", "Procfile", "--output-format", "json"]
  working_dir = "${path.module}/.."
}

# All env values (public + secret) for this environment, resolved from
# .env.production.backend via 1Password and emitted as JSON.
# Inspect with: ./scripts/export-env.sh ../.env.production.backend
data "external" "secrets" {
  program     = ["bash", "${path.module}/scripts/export-env.sh", "${path.module}/../.env.production.backend"]
  working_dir = path.module
}

# A physically separate public export. It must contain only the allowlisted
# public keys above; startup validation independently rejects forbidden drift.
data "external" "public_secrets" {
  program     = ["bash", "${path.module}/scripts/export-env.sh", "${path.module}/../.env.production.public"]
  working_dir = path.module
}
