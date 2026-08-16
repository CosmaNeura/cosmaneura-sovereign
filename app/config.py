"""
Central configuration for the CosmaNeura Sovereign service.

Two things live here:
  1. `Settings` — environment-driven secrets/endpoints (12-factor style).
  2. `ComplianceProfile` — the patient vs. provider split that decides
     HIPAA posture, audit behavior, and which components may see PHI.

Nothing in this file is doctrine-specific; doctrine lives in the arbiter.
"""
from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Personas
# ---------------------------------------------------------------------------
class Persona(str, Enum):
    PATIENT = "patient"      # consumer-facing build  -> SOC 2, NOT HIPAA
    PROVIDER = "provider"    # clinician-facing build -> SOC 2 AND HIPAA


# ---------------------------------------------------------------------------
# Sovereign composition
# ---------------------------------------------------------------------------
class SovereignMode(str, Enum):
    """
    How the Sovereign's two jobs (distribute the task / render final judgment)
    are split across components.

    HYBRID (default, recommended): a fast reasoning LLM distributes the task
        (routing) and Magisterium renders final doctrinal judgment. Faithful to
        Hobbes (law + supreme court vs. administration), accurate, and fast --
        pure-logistics requests never pay for a Magisterium round-trip.

    LITERAL: Magisterium is consulted on *every* request as the task-distributor
        as well as the final judge -- the "Magisterium is the Sovereign" reading.
        This adds a Magisterium round-trip to every call and still needs a small
        mechanical step to turn Magisterium's prose into a routing decision, so
        it is slower and more awkward for logistics. Provided as a one-setting
        switch; HYBRID is recommended for production.
    """
    HYBRID = "hybrid"
    LITERAL = "literal"


class AuthMode(str, Enum):
    """
    How callers are authenticated and how persona is determined.

    DISABLED:  no actor token required. Safe only for an anonymous public
               pinned mount; request persona never grants authority.
    DEV_HS256: shared-secret tokens for local testing without an IdP. Not prod.
    OIDC:      production. Verify RS256/ES256 against the IdP's JWKS, plus
               issuer/audience/expiry. Persona derived from a verified claim.
    """
    DISABLED = "disabled"
    DEV_HS256 = "dev_hs256"
    OIDC = "oidc"


# ---------------------------------------------------------------------------
# Settings (read from environment / .env)  -- see .env.example
# ---------------------------------------------------------------------------
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Magisterium (doctrinal authority + final arbiter) -----------------
    # OpenAI-compatible. Base + model verified from Magisterium developer docs.
    magisterium_api_key: Optional[str] = Field(default=None)
    magisterium_base_url: str = "https://www.magisterium.com/api/v1"
    magisterium_model: str = "magisterium-1"
    magisterium_mcp_url: str = "https://mcp.magisterium.com"
    # Optional ADDITIVE doctrinal layer: let agents consult Magisterium's MCP
    # server inline (via Anthropic's MCP connector) while they reason, on top of
    # the final REST arbitration gate. OFF by default.
    #
    # ⚠️ COMPLIANCE: Anthropic's MCP connector is NOT ZDR-eligible and is NOT
    # available on Bedrock/Vertex, and Magisterium is not a business associate.
    # Therefore this layer may only see DE-IDENTIFIED, non-PHI text. The LLM
    # client enforces this with an assert_no_phi guard; the PHI-bearing
    # engagement agent never enables it.
    magisterium_mcp_enabled: bool = False
    # OAuth bearer token for the Magisterium MCP server (requires a
    # magisterium.com account; pre-acquired out of band -- the connector does
    # not run the OAuth flow itself).
    magisterium_mcp_token: Optional[str] = Field(default=None)
    # Anthropic MCP-connector beta header. Confirm current value at
    # https://platform.claude.com/docs/en/agents-and-tools/mcp-connector
    anthropic_mcp_beta: str = "mcp-client-2025-11-20"

    # --- Orchestrator / agent reasoning LLM (the Sovereign's "brain") ------
    # Defaults to Anthropic's Messages API because it is BAA-eligible (1P API
    # + ZDR) for the provider/HIPAA build and is SOC 2 Type II.
    # NOTE: confirm current model IDs at
    #   https://docs.claude.com/en/docs/about-claude/models
    # "anthropic" or "azure_openai". Both are BAA-eligible for the provider
    # build: Anthropic 1P API + ZDR, or Azure OpenAI under the Azure BAA (the
    # same path the main CosmaNeura app already uses).
    llm_provider: str = "anthropic"
    anthropic_api_key: Optional[str] = Field(default=None)
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_version: str = "2023-06-01"
    reasoning_model: str = "claude-sonnet-4-6"          # routing-aware reasoning
    fast_model: str = "claude-haiku-4-5-20251001"       # cheap classification

    # Azure OpenAI (llm_provider="azure_openai"). reasoning_model / fast_model
    # are interpreted as Azure *deployment* names (e.g. "gpt-4.1-2025-04-14").
    azure_openai_endpoint: Optional[str] = Field(default=None)
    azure_openai_api_key: Optional[str] = Field(default=None)
    azure_openai_api_version: str = "2025-03-01-preview"

    # --- De-identification (PHI firewall) ----------------------------------
    # "regex" = the built-in baseline (obvious identifiers only, no names).
    # "azure" = Azure Health Data Services de-identification (HIPAA-grade,
    # catches names/locations/etc). The same service the main app uses.
    deid_provider: str = "regex"
    azure_deid_endpoint: Optional[str] = Field(default=None)
    azure_deid_api_version: str = "2024-11-15"
    # Auth: if the client secret is set, use a service principal (matches the
    # main app). Otherwise DefaultAzureCredential — az login locally, managed
    # identity in Azure Container Apps (no stored secret).
    azure_deid_tenant_id: Optional[str] = Field(default=None)
    azure_deid_client_id: Optional[str] = Field(default=None)
    azure_deid_client_secret: Optional[str] = Field(default=None)

    # --- Sovereign composition ---------------------------------------------
    # "hybrid" (recommended): LLM routes, Magisterium is final judge.
    # "literal": Magisterium is consulted on every request (slower). See
    # SovereignMode above and ARCHITECTURE.md section 1.
    sovereign_mode: SovereignMode = SovereignMode.HYBRID

    # --- Public data sources (free, no key) --------------------------------
    nppes_base_url: str = "https://npiregistry.cms.hhs.gov/api"
    clinicaltrials_base_url: str = "https://clinicaltrials.gov/api/v2"
    # PubMed/PMC via NCBI E-utilities. Free; the optional API key only raises
    # rate limits. Reaches the Catholic bioethics corpus too (Linacre Quarterly).
    pubmed_base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    pubmed_api_key: Optional[str] = Field(default=None)

    # --- EMR lab results (athenahealth) ------------------------------------
    # Whole capability off by default: this ships as a sandbox demo feature and
    # must be excludable from a production build until the pilot practice goes
    # live (BAA signed + the app enabled in athenaOne Certified API Access).
    feature_athena_labs: bool = False
    athena_client_id: Optional[str] = Field(default=None)
    athena_client_secret: Optional[str] = Field(default=None)
    athena_env: str = "preview"            # preview | production
    athena_practice_id: str = "195900"     # athenahealth shared sandbox practice
    athena_department_id: str = "1"
    # Patient lookup by NAME lets a model reach any patient in the practice,
    # which would bypass the host app's own authorization. Sandbox only unless
    # a deployment deliberately opts in.
    labs_allow_patient_search: bool = False

    # --- Partner directory (MyCatholicDoctor + resource partners) ----------
    # Until MyCatholicDoctor exposes a feed/API, the adapter reads a local
    # JSON fixture (see app/clients/partner_directory.py).
    partner_directory_url: Optional[str] = Field(default=None)
    partner_directory_api_key: Optional[str] = Field(default=None)
    # Local JSON produced by tools/scrape_mycatholicdoctor.py. Since MCD has no
    # API, scraping the public directory into a file is the interim feed. Takes
    # precedence over the bundled fixture; the remote URL still wins over both.
    partner_directory_path: Optional[str] = Field(default=None)

    # --- Runtime knobs -----------------------------------------------------
    # When true, LLM + Magisterium calls return deterministic stubs so the
    # whole pipeline runs with zero credentials. Flip to false in real envs.
    mock_mode: bool = True
    request_timeout_seconds: float = 30.0

    # Serve the built-in chat demo UI at GET /demo. Off by default; enable only
    # for local demos (the launch script tools/run_demo_web.sh sets it).
    demo_ui_enabled: bool = False

    # Display name used in the served greeting (GET /welcome). Becomes
    # per-tenant branding in the Phase 2 tenant layer.
    partner_name: str = "CosmaNeura"

    # Audit sink: "stdout" for local dev, "file" writes JSONL to audit_path.
    # In production point this at an append-only, access-controlled store.
    audit_sink: str = "stdout"
    audit_path: str = "./audit_log.jsonl"

    # Legacy deployment flag retained for compatibility with existing launch
    # configuration. It no longer grants provider authority; mount context is
    # the sole authorization axis.
    allow_provider_persona: bool = False

    # --- Deployment-scoped mount authority --------------------------------
    # ADR 0014 permits one fixed mount per deployment until the platform mount
    # resolver is live. These values are server configuration; no request body
    # field can override them. PR 2 will replace the individual fields with
    # named deployment profiles and stricter startup validation.
    mount_surface: str = "public"
    mount_auth_tier: str = "anonymous"
    mount_capabilities: list[str] = Field(default_factory=lambda: [
        "education:answer",
        "values:answer",
        "referrals:search",
        "intake:invite",
    ])
    mount_tenant_id: Optional[str] = None

    # Explicit legal attestation that the configured LLM inference path is
    # covered by an executed BAA with Zero Data Retention (or an equivalent
    # hyperscaler BAA). Using provider "anthropic" alone is NOT sufficient --
    # a BAA must actually be signed. The PHI path refuses to run without this.
    baa_attested: bool = False

    # --- Authentication ----------------------------------------------------
    # See AuthMode. Provider/PHI deployments MUST use "oidc" (persona bound to a
    # verified identity); "disabled" is patient-only/local dev.
    auth_mode: AuthMode = AuthMode.DISABLED
    # OIDC (production) — from your identity provider (Auth0/Cognito/Entra/Okta/…)
    oidc_issuer: Optional[str] = None
    oidc_audience: Optional[str] = None
    oidc_jwks_url: Optional[str] = None
    oidc_algorithms: list[str] = Field(default_factory=lambda: ["RS256"])
    # Claim → persona mapping. role_claim may be dotted (e.g. realm_access.roles).
    auth_role_claim: str = "roles"
    auth_provider_role: str = "provider"
    # Dev-only shared secret for AUTH_MODE=dev_hs256. Never set in production.
    auth_dev_secret: Optional[str] = Field(default=None)

    # Browser CORS allowlist. Empty = no cross-origin browser access (default).
    # Set to explicit origins for a browser-facing deployment; never use "*".
    cors_allow_origins: list[str] = Field(default_factory=list)


@lru_cache
def get_settings() -> Settings:
    return Settings()


# ---------------------------------------------------------------------------
# Compliance profiles
# ---------------------------------------------------------------------------
class ComplianceProfile:
    """
    Resolves a Persona into a concrete set of guarantees the pipeline enforces.

    The single most important field is `phi_boundary_enforced`: when true,
    the PHI firewall scrubs identifiers before *any* call leaves the
    BAA-covered boundary (Magisterium, ClinicalTrials.gov, NPPES, web search).
    """

    def __init__(self, persona: Persona):
        self.persona = persona

        if persona == Persona.PROVIDER:
            self.hipaa = True
            self.soc2 = True
            self.phi_allowed = True               # provider may submit PHI
            self.phi_boundary_enforced = True     # scrub before non-BAA hops
            self.require_baa_covered_llm = True    # inference must be under BAA
            self.audit_required = True
            self.audit_retention_days = 90        # >= SOC2 / HIPAA practice
        else:  # PATIENT
            self.hipaa = False
            self.soc2 = True
            # A consumer volunteering their own info is not a covered entity
            # relationship, so this is health data but not "PHI" in the HIPAA
            # sense. We still treat it as sensitive and minimize exposure.
            self.phi_allowed = False
            self.phi_boundary_enforced = True     # still scrub before non-BAA
            self.require_baa_covered_llm = False
            self.audit_required = True            # lighter audit, still on
            self.audit_retention_days = 30

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"ComplianceProfile(persona={self.persona.value}, hipaa={self.hipaa}, "
            f"soc2={self.soc2}, phi_boundary_enforced={self.phi_boundary_enforced})"
        )
