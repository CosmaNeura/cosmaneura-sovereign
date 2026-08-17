variable "tags" {
  description = "Tags applied to all resources"
  type        = map(string)
  default = {
    environment = "production"
    service     = "cosmaneura-sovereign"
    managed     = "terraform"
  }
}

# GitHub repo (OWNER/REPO) allowed to deploy through the protected production
# Environment's OIDC subject.
variable "github_repository" {
  description = "GitHub repo authorized to push images / deploy, as OWNER/REPO"
  type        = string
  default     = "CosmaNeura/cosmaneura-sovereign"
}

variable "key_vault_secrets_ready" {
  description = "Operator assertion that both mount-specific vaults contain every required runtime secret"
  type        = bool
  default     = false
}

variable "live_apps_enabled" {
  description = "Explicit release gate for creating both externally reachable Container Apps"
  type        = bool
  default     = false
}

variable "initial_image_ref" {
  description = "Immutable ACR fallback image used to create or recover both mounts; persist and refresh before any later infrastructure plan"
  type        = string
  default     = ""

  validation {
    condition = (
      var.initial_image_ref == "" ||
      can(regex("@sha256:[0-9a-f]{64}$", var.initial_image_ref))
    )
    error_message = "initial_image_ref must be empty for resource bootstrap or end in an immutable sha256 digest."
  }
}

variable "bootstrap_operator_object_id" {
  description = "Temporary Entra object ID allowed to push the first image and seed both vaults"
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.bootstrap_operator_object_id == null ||
      can(regex("^[0-9a-fA-F-]{36}$", var.bootstrap_operator_object_id))
    )
    error_message = "bootstrap_operator_object_id must be null or a UUID-shaped Entra object ID."
  }
}

# ENV vars that must stay secret. Terraform stores only their versionless Key
# Vault URIs; values are seeded with scripts/sync-key-vault-secrets.sh and never
# enter Terraform state.
variable "sovereign_secret_vars" {
  description = "ENV vars kept as Container App secrets"
  type        = set(string)
  default = [
    "MAGISTERIUM_API_KEY",
    # LLM inference. Azure OpenAI is the configured path (BAA-covered under the
    # Azure BAA, reuses the main app's resource). For Anthropic instead, swap
    # this for "ANTHROPIC_API_KEY".
    "AZURE_OPENAI_API_KEY",
    # Provider-only synthetic Preview EHR credentials. These names are absent
    # from both public allowlists below.
    "ATHENA_CLIENT_ID",
    "ATHENA_CLIENT_SECRET",
    "SOVEREIGN_RECORD_GRANT_SECRET",
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
    # Synthetic lab pilot; startup pins these to Preview practice 195900.
    "FEATURE_ATHENA_LABS", "ATHENA_ENV", "ATHENA_PRACTICE_ID",
    "ATHENA_DEPARTMENT_ID", "LABS_ALLOW_PATIENT_SEARCH",
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
  program = [
    "python3",
    "${path.module}/scripts/read-procfile.py",
    "${path.module}/../Procfile",
  ]
  working_dir = "${path.module}/.."
}

# Only non-secret provider configuration enters Terraform state. The exporter
# refuses op:// references and fails if an allowlisted key is absent.
data "external" "provider_public_config" {
  count = var.live_apps_enabled ? 1 : 0
  program = [
    "bash",
    "${path.module}/scripts/export-public-env.sh",
    "${path.module}/../.env.production.backend",
    join(",", sort(tolist(var.sovereign_public_vars))),
    "provider",
  ]
  working_dir = path.module
}

# The public configuration is independently filtered, so provider-only names
# cannot cross into its Terraform state or Container App environment.
data "external" "public_config" {
  count = var.live_apps_enabled ? 1 : 0
  program = [
    "bash",
    "${path.module}/scripts/export-public-env.sh",
    "${path.module}/../.env.production.public",
    join(",", sort(tolist(var.public_public_vars))),
    "public",
  ]
  working_dir = path.module
}
