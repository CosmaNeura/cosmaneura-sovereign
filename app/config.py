"""
Central configuration for the CosmaNeura Sovereign service.

Two things live here:
  1. `Settings` — environment-driven secrets/endpoints (12-factor style).
  2. `ComplianceProfile` — the patient vs. provider split that decides
     HIPAA posture, audit behavior, and which components may see PHI.

Nothing in this file is doctrine-specific; doctrine lives in the arbiter.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
import re
from typing import Literal, Optional
from urllib.parse import urlparse

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.security.record_grant import (
    RecordGrantConfigurationError,
    configured_record_grant_key,
)


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


class MountProfile(str, Enum):
    """The one mount a Sovereign process is allowed to serve."""

    PUBLIC = "public"
    PROVIDER_ASSISTANT = "provider_assistant"


@dataclass(frozen=True)
class DeploymentMount:
    """Immutable authority values selected by ``MOUNT_PROFILE``."""

    surface: str
    auth_tier: str
    capabilities: tuple[str, ...]


DEPLOYMENT_MOUNTS: dict[MountProfile, DeploymentMount] = {
    MountProfile.PUBLIC: DeploymentMount(
        surface="public",
        auth_tier="anonymous",
        capabilities=(
            "education:answer",
            "values:answer",
            "referrals:search",
            "intake:invite",
        ),
    ),
    MountProfile.PROVIDER_ASSISTANT: DeploymentMount(
        surface="provider_assistant",
        auth_tier="provider",
        capabilities=(
            "education:answer",
            "referrals:search",
            "ethics:consult",
            "visit-prep:run",
            "plan-workbench:run",
            "ehr:labs:read",
            "journey-digest:write",
        ),
    ),
}


# The operational provider beta can only narrow platform-issued authority.
# Expanding the platform grant cannot silently enable an unlaunched workflow.
PROVIDER_BETA_CAPABILITIES = frozenset({
    "education:answer",
    "ethics:consult",
    "visit-prep:run",
    "session-prep:read",
})


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
    # Per-read inactivity budget for REST responses, not a whole-turn deadline.
    # Connect/write/pool continue to use request_timeout_seconds.
    magisterium_read_timeout_seconds: float = Field(
        default=90.0, ge=1.0, le=120.0, allow_inf_nan=False,
    )
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
    # Dormant Visit Prep patient clarification; separate from provider Session Prep.
    visit_prep_patient_clarification_enabled: bool = False

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
    # Provider-only shared key used to authenticate the platform's short-lived
    # open-record grant. The public deployment must never possess it.
    sovereign_record_grant_secret: Optional[SecretStr] = Field(default=None)
    athena_env: str = "preview"            # preview | production
    athena_practice_id: str = "195900"     # athenahealth shared sandbox practice
    athena_department_id: str = "1"
    # Retained so an old deployment fails loudly. Name search is not part of
    # the governed labs.read operation and is forbidden at every tier.
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

    # Legacy local sink. When the provider platform boundary is enabled,
    # only value-free operational stderr is emitted here and the platform owns
    # durable audit ingestion. The local chain has no production integrity claim.
    audit_sink: str = "stdout"
    audit_path: str = "./audit_log.jsonl"

    # Legacy deployment flag retained for compatibility with existing launch
    # configuration. It no longer grants provider authority; mount context is
    # the sole authorization axis.
    allow_provider_persona: bool = False

    # --- Deployment-scoped mount authority --------------------------------
    # Exactly one named profile is selected for the lifetime of the process.
    # Surface, tier, and capabilities come from DEPLOYMENT_MOUNTS and cannot be
    # widened by environment variables or request data.
    mount_profile: MountProfile = MountProfile.PUBLIC
    mount_tenant_id: Optional[str] = None
    # Accepted only so an old deployment fails loudly at startup instead of
    # silently believing these legacy authority overrides still work.
    mount_surface: Optional[str] = None
    mount_auth_tier: Optional[str] = None
    mount_capabilities: Optional[list[str]] = None

    # --- Platform authority and observational audit ----------------------
    # Provider-first platform rollout. When enabled, the opaque platform mount
    # context is the authority source and each completed turn is durably
    # reported to the platform Agent API with the same interaction lineage.
    # The sole Authorization bearer is that opaque mount token. Direct Clerk
    # JWT verification exists only when this boundary is disabled.
    platform_agent_api_enabled: bool = False
    platform_agent_api_base_url: Optional[str] = None
    platform_agent_api_scope: Optional[str] = None
    platform_workload_client_id: Optional[str] = None
    # Total budget for one canonical phase or observational write, including
    # same-key retries. Keep telemetry failure from consuming the chat timeout.
    platform_write_timeout_seconds: float = Field(default=3.0, ge=0.1, le=10.0)

    # Session Prep's first operational slice is intentionally restricted to one
    # explicitly configured, server-only synthetic staging handle. It must never
    # be projected into host hydration or browser requests. The feature remains
    # off by default and has no real-PHI mode; broader activation requires a
    # later reviewed control change.
    feature_session_prep: bool = False
    session_prep_context_source: Literal["synthetic", "platform"] = "synthetic"
    session_prep_synthetic_context_ref: Optional[str] = None

    # Anonymous public traffic is limited in-process before any model call.
    # Container Apps is configured with a single public replica until a shared
    # rate-limit store is introduced.
    public_chat_rate_limit_requests: int = Field(default=30, ge=1, le=10_000)
    public_chat_rate_limit_window_seconds: float = Field(
        default=60.0, gt=0.0, le=86_400.0,
    )
    public_chat_rate_limit_max_clients: int = Field(
        default=10_000, ge=100, le=1_000_000,
    )
    # Trust only when a deployment-controlled reverse proxy appends the
    # right-most X-Forwarded-For hop (Azure Container Apps does).
    public_chat_trust_forwarded_for: bool = False

    # Explicit legal attestation that the configured LLM inference path is
    # covered by an executed BAA with Zero Data Retention (or an equivalent
    # hyperscaler BAA). Using provider "anthropic" alone is NOT sufficient --
    # a BAA must actually be signed. The PHI path refuses to run without this.
    baa_attested: bool = False

    # --- Authentication ----------------------------------------------------
    # Platform-authoritative provider mounts MUST use "disabled": their sole
    # inbound bearer is an opaque mount token, resolved by the platform. Legacy
    # provider mounts verify the browser actor directly and use OIDC in live
    # deployments. Public/local development also uses "disabled" by default.
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

    @property
    def deployment_mount(self) -> DeploymentMount:
        """Return the fixed mount selected by the deployment profile."""
        return DEPLOYMENT_MOUNTS[self.mount_profile]


class DeploymentConfigurationError(RuntimeError):
    """A deployment profile contains authority it must not possess."""


def _has_value(value: Optional[str]) -> bool:
    return value is not None and bool(value.strip())


def _secret_value(value: Optional[SecretStr]) -> Optional[str]:
    return value.get_secret_value() if value is not None else None


def _cors_origin_is_exact(origin: str, *, mock_mode: bool) -> bool:
    if not origin or origin != origin.strip() or origin == "*":
        return False
    try:
        parsed = urlparse(origin)
        port = parsed.port
    except ValueError:
        return False
    if (
        origin != f"{parsed.scheme}://{parsed.netloc}"
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.params
        or parsed.query
        or parsed.fragment
        or "*" in parsed.hostname
    ):
        return False
    if parsed.scheme == "https":
        return port is None or 1 <= port <= 65_535
    return (
        mock_mode
        and parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        and (port is None or 1 <= port <= 65_535)
    )


def validate_deployment_configuration(settings: Settings) -> None:
    """Fail boot on profile drift before the HTTP service starts accepting work."""
    if (
        len(settings.cors_allow_origins) != len(set(settings.cors_allow_origins))
        or any(
            not _cors_origin_is_exact(origin, mock_mode=settings.mock_mode)
            for origin in settings.cors_allow_origins
        )
    ):
        raise DeploymentConfigurationError(
            "CORS_ALLOW_ORIGINS must contain unique, exact HTTPS origins; "
            "wildcards, paths, credentials, queries, and fragments are forbidden."
        )

    legacy = [
        name
        for name, value in (
            ("MOUNT_SURFACE", settings.mount_surface),
            ("MOUNT_AUTH_TIER", settings.mount_auth_tier),
            ("MOUNT_CAPABILITIES", settings.mount_capabilities),
        )
        if value is not None
    ]
    if legacy:
        raise DeploymentConfigurationError(
            "Legacy mount authority settings are forbidden; select only "
            f"MOUNT_PROFILE. Remove: {', '.join(legacy)}."
        )

    ehr_configuration = [
        name
        for name, configured in (
            ("FEATURE_ATHENA_LABS", settings.feature_athena_labs),
            ("ATHENA_CLIENT_ID", _has_value(settings.athena_client_id)),
            ("ATHENA_CLIENT_SECRET", _has_value(settings.athena_client_secret)),
            ("LABS_ALLOW_PATIENT_SEARCH", settings.labs_allow_patient_search),
        )
        if configured
    ]
    if ehr_configuration and settings.athena_env != "preview":
        raise DeploymentConfigurationError(
            "EHR configuration is permitted only with ATHENA_ENV=preview. "
            "Remove or correct: ATHENA_ENV, "
            + ", ".join(ehr_configuration)
            + "."
        )
    if settings.labs_allow_patient_search:
        raise DeploymentConfigurationError(
            "LABS_ALLOW_PATIENT_SEARCH must be false; patient name search is "
            "not part of the governed labs.read operation at any tier."
        )
    if settings.feature_athena_labs and settings.athena_practice_id != "195900":
        raise DeploymentConfigurationError(
            "Sandbox labs require ATHENA_PRACTICE_ID=195900."
        )
    if settings.feature_athena_labs and not settings.mock_mode:
        missing_labs = [
            name
            for name, value in (
                ("ATHENA_CLIENT_ID", settings.athena_client_id),
                ("ATHENA_CLIENT_SECRET", settings.athena_client_secret),
            )
            if not _has_value(value)
        ]
        if missing_labs:
            raise DeploymentConfigurationError(
                "Non-mock sandbox labs are missing required credentials: "
                + ", ".join(missing_labs)
                + "."
            )
        try:
            configured_record_grant_key(
                _secret_value(settings.sovereign_record_grant_secret)
            )
        except RecordGrantConfigurationError:
            raise DeploymentConfigurationError(
                "Non-mock sandbox labs require a canonical "
                "SOVEREIGN_RECORD_GRANT_SECRET."
            ) from None

    if settings.mount_profile == MountProfile.PUBLIC:
        forbidden: list[str] = []
        if settings.auth_mode != AuthMode.DISABLED:
            forbidden.append("AUTH_MODE")
        if _has_value(settings.auth_dev_secret):
            forbidden.append("AUTH_DEV_SECRET")
        if _has_value(settings.oidc_issuer):
            forbidden.append("OIDC_ISSUER")
        if _has_value(settings.oidc_audience):
            forbidden.append("OIDC_AUDIENCE")
        if _has_value(settings.oidc_jwks_url):
            forbidden.append("OIDC_JWKS_URL")
        if _has_value(settings.mount_tenant_id):
            forbidden.append("MOUNT_TENANT_ID")
        if settings.allow_provider_persona:
            forbidden.append("ALLOW_PROVIDER_PERSONA")
        if settings.baa_attested:
            forbidden.append("BAA_ATTESTED")
        if settings.feature_athena_labs:
            forbidden.append("FEATURE_ATHENA_LABS")
        if _has_value(settings.athena_client_id):
            forbidden.append("ATHENA_CLIENT_ID")
        if _has_value(settings.athena_client_secret):
            forbidden.append("ATHENA_CLIENT_SECRET")
        if _has_value(_secret_value(settings.sovereign_record_grant_secret)):
            forbidden.append("SOVEREIGN_RECORD_GRANT_SECRET")
        if settings.labs_allow_patient_search:
            forbidden.append("LABS_ALLOW_PATIENT_SEARCH")
        if settings.platform_agent_api_enabled:
            forbidden.append("PLATFORM_AGENT_API_ENABLED")
        if settings.feature_session_prep:
            forbidden.append("FEATURE_SESSION_PREP")
        if settings.session_prep_context_source != "synthetic":
            forbidden.append("SESSION_PREP_CONTEXT_SOURCE")
        if _has_value(settings.session_prep_synthetic_context_ref):
            forbidden.append("SESSION_PREP_SYNTHETIC_CONTEXT_REF")
        for name, value in (
            ("PLATFORM_AGENT_API_BASE_URL", settings.platform_agent_api_base_url),
            ("PLATFORM_AGENT_API_SCOPE", settings.platform_agent_api_scope),
            ("PLATFORM_WORKLOAD_CLIENT_ID", settings.platform_workload_client_id),
        ):
            if _has_value(value):
                forbidden.append(name)
        if settings.deid_provider.strip().lower() != "regex":
            forbidden.append("DEID_PROVIDER")
        for name, value in (
            ("AZURE_DEID_ENDPOINT", settings.azure_deid_endpoint),
            ("AZURE_DEID_TENANT_ID", settings.azure_deid_tenant_id),
            ("AZURE_DEID_CLIENT_ID", settings.azure_deid_client_id),
            ("AZURE_DEID_CLIENT_SECRET", settings.azure_deid_client_secret),
        ):
            if _has_value(value):
                forbidden.append(name)
        if forbidden:
            raise DeploymentConfigurationError(
                "MOUNT_PROFILE=public cannot start with forbidden configuration: "
                + ", ".join(forbidden)
                + "."
            )
        return

    if not _has_value(settings.mount_tenant_id):
        raise DeploymentConfigurationError(
            "MOUNT_PROFILE=provider_assistant requires MOUNT_TENANT_ID."
        )
    if settings.feature_session_prep:
        context_ref = settings.session_prep_synthetic_context_ref or ""
        if not settings.platform_agent_api_enabled:
            raise DeploymentConfigurationError(
                "FEATURE_SESSION_PREP requires platform Agent API authority."
            )
        if settings.session_prep_context_source == "platform":
            if _has_value(settings.session_prep_synthetic_context_ref):
                raise DeploymentConfigurationError(
                    "Platform Session Prep cannot use a fixed synthetic context."
                )
        elif not re.fullmatch(
            r"context_synthetic_[A-Za-z0-9._~-]{1,142}", context_ref,
        ):
            raise DeploymentConfigurationError(
                "FEATURE_SESSION_PREP requires one exact synthetic staging "
                "SESSION_PREP_SYNTHETIC_CONTEXT_REF."
            )
    elif settings.session_prep_context_source != "synthetic":
        raise DeploymentConfigurationError(
            "SESSION_PREP_CONTEXT_SOURCE=platform requires FEATURE_SESSION_PREP=true."
        )
    elif _has_value(settings.session_prep_synthetic_context_ref):
        raise DeploymentConfigurationError(
            "SESSION_PREP_SYNTHETIC_CONTEXT_REF requires FEATURE_SESSION_PREP=true."
        )
    if settings.platform_agent_api_enabled:
        if settings.auth_mode != AuthMode.DISABLED:
            raise DeploymentConfigurationError(
                "Platform authority requires AUTH_MODE=disabled so the sole "
                "Authorization bearer is the opaque mount token."
            )
    else:
        if settings.auth_mode == AuthMode.DISABLED:
            raise DeploymentConfigurationError(
                "A legacy provider mount requires authenticated actors; "
                "AUTH_MODE cannot be disabled."
            )
        if not settings.mock_mode and settings.auth_mode != AuthMode.OIDC:
            raise DeploymentConfigurationError(
                "A non-mock legacy provider deployment requires AUTH_MODE=oidc."
            )
    if not settings.mock_mode:
        if not settings.baa_attested:
            raise DeploymentConfigurationError(
                "A non-mock provider deployment requires BAA_ATTESTED=true."
            )
        if not settings.platform_agent_api_enabled:
            missing_oidc = [
                name
                for name, value in (
                    ("OIDC_ISSUER", settings.oidc_issuer),
                    ("OIDC_AUDIENCE", settings.oidc_audience),
                    ("OIDC_JWKS_URL", settings.oidc_jwks_url),
                    ("AUTH_PROVIDER_ROLE", settings.auth_provider_role),
                )
                if not _has_value(value)
            ]
            if missing_oidc:
                raise DeploymentConfigurationError(
                    "A non-mock legacy provider deployment is missing required "
                    "OIDC configuration: " + ", ".join(missing_oidc) + "."
                )
        if settings.deid_provider != "azure":
            raise DeploymentConfigurationError(
                "A non-mock provider deployment requires DEID_PROVIDER=azure."
            )
        if not _has_value(settings.azure_deid_endpoint):
            raise DeploymentConfigurationError(
                "A non-mock provider deployment requires AZURE_DEID_ENDPOINT."
            )
        try:
            configured_record_grant_key(
                _secret_value(settings.sovereign_record_grant_secret)
            )
        except RecordGrantConfigurationError:
            raise DeploymentConfigurationError(
                "A non-mock provider deployment requires a canonical "
                "SOVEREIGN_RECORD_GRANT_SECRET."
            ) from None

    if settings.platform_agent_api_enabled:
        missing_platform = [
            name
            for name, value in (
                ("PLATFORM_AGENT_API_BASE_URL", settings.platform_agent_api_base_url),
                ("PLATFORM_AGENT_API_SCOPE", settings.platform_agent_api_scope),
                ("PLATFORM_WORKLOAD_CLIENT_ID", settings.platform_workload_client_id),
            )
            if not _has_value(value)
        ]
        if missing_platform:
            raise DeploymentConfigurationError(
                "Platform authority is missing required configuration: "
                + ", ".join(missing_platform)
                + "."
            )
        try:
            parsed_platform = urlparse(settings.platform_agent_api_base_url or "")
        except ValueError:
            parsed_platform = urlparse("")
        if (
            parsed_platform.scheme != "https"
            or not parsed_platform.hostname
            or parsed_platform.username is not None
            or parsed_platform.password is not None
            or parsed_platform.path not in {"", "/"}
            or parsed_platform.params
            or parsed_platform.query
            or parsed_platform.fragment
        ):
            raise DeploymentConfigurationError(
                "PLATFORM_AGENT_API_BASE_URL must be an exact HTTPS origin."
            )
        scope = settings.platform_agent_api_scope or ""
        if not scope.startswith("api://") or not scope.endswith("/.default"):
            raise DeploymentConfigurationError(
                "PLATFORM_AGENT_API_SCOPE must be an api:// application scope "
                "ending in /.default."
            )


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
