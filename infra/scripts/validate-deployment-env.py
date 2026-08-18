#!/usr/bin/env python3
"""Validate one deployment env file without reading or printing secret values."""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


PROVIDER_PUBLIC = {
    "MOCK_MODE", "SOVEREIGN_MODE", "MOUNT_PROFILE", "MOUNT_TENANT_ID",
    "AUTH_MODE", "OIDC_ISSUER", "OIDC_JWKS_URL", "OIDC_AUDIENCE",
    "OIDC_ALGORITHMS", "AUTH_ROLE_CLAIM", "AUTH_PROVIDER_ROLE",
    "ALLOW_PROVIDER_PERSONA", "BAA_ATTESTED", "DEID_PROVIDER",
    "LLM_PROVIDER", "AZURE_OPENAI_API_VERSION",
    "AZURE_OPENAI_ENDPOINT", "REASONING_MODEL", "FAST_MODEL",
    "MAGISTERIUM_BASE_URL", "MAGISTERIUM_MODEL", "MAGISTERIUM_MCP_ENABLED",
    "ANTHROPIC_MCP_BETA", "NPPES_BASE_URL", "CLINICALTRIALS_BASE_URL",
    "AUDIT_SINK", "AUDIT_PATH", "CORS_ALLOW_ORIGINS",
    "FEATURE_ATHENA_LABS", "ATHENA_ENV", "ATHENA_PRACTICE_ID",
    "ATHENA_DEPARTMENT_ID", "LABS_ALLOW_PATIENT_SEARCH",
}
PROVIDER_SECRETS = {
    "MAGISTERIUM_API_KEY", "AZURE_OPENAI_API_KEY",
    "ATHENA_CLIENT_ID", "ATHENA_CLIENT_SECRET",
    "SOVEREIGN_RECORD_GRANT_SECRET",
}
PUBLIC_PUBLIC = {
    "MOCK_MODE", "SOVEREIGN_MODE", "LLM_PROVIDER", "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_VERSION", "REASONING_MODEL", "FAST_MODEL",
    "MAGISTERIUM_BASE_URL", "MAGISTERIUM_MODEL", "MAGISTERIUM_MCP_ENABLED",
    "ANTHROPIC_MCP_BETA", "NPPES_BASE_URL", "CLINICALTRIALS_BASE_URL",
    "AUDIT_SINK", "AUDIT_PATH", "CORS_ALLOW_ORIGINS",
    "PUBLIC_CHAT_RATE_LIMIT_REQUESTS", "PUBLIC_CHAT_RATE_LIMIT_WINDOW_SECONDS",
    "PUBLIC_CHAT_RATE_LIMIT_MAX_CLIENTS",
}
PUBLIC_SECRETS = {"MAGISTERIUM_API_KEY", "AZURE_OPENAI_API_KEY"}

SECRET_ITEMS = {
    "provider": {
        "MAGISTERIUM_API_KEY": "magisterium-api/credential",
        "AZURE_OPENAI_API_KEY": "CosmasCode/credential",
        "ATHENA_CLIENT_ID": "athenahealth-preview/client-id",
        "ATHENA_CLIENT_SECRET": "athenahealth-preview/client-secret",
        "SOVEREIGN_RECORD_GRANT_SECRET": "sovereign-record-grant/credential",
    },
    "public": {
        "MAGISTERIUM_API_KEY": "magisterium-public/credential",
        "AZURE_OPENAI_API_KEY": "CosmasCode-public/credential",
    },
}
ONEPASSWORD_VAULTS = {"prod": "Production", "staging": "Staging"}

# Staging has one accepted, public Clerk authority. Keep this environment-scoped:
# production must continue to take its distinct issuer and JWKS URL from the
# reviewed production deployment file.
STAGING_PROVIDER_AUTH = {
    "OIDC_ISSUER": "https://resolved-emu-53.clerk.accounts.dev",
    "OIDC_JWKS_URL": (
        "https://resolved-emu-53.clerk.accounts.dev/.well-known/jwks.json"
    ),
    "OIDC_AUDIENCE": "cosmaneura-sovereign",
    "AUTH_ROLE_CLAIM": "roles",
    "AUTH_PROVIDER_ROLE": "provider",
}

FIXED = {
    "provider": {
        "MOCK_MODE": "false",
        "SOVEREIGN_MODE": "hybrid",
        "MOUNT_PROFILE": "provider_assistant",
        "AUTH_MODE": "oidc",
        "OIDC_AUDIENCE": "cosmaneura-sovereign",
        "AUTH_ROLE_CLAIM": "roles",
        "AUTH_PROVIDER_ROLE": "provider",
        "ALLOW_PROVIDER_PERSONA": "false",
        "BAA_ATTESTED": "true",
        "DEID_PROVIDER": "azure",
        "LLM_PROVIDER": "azure_openai",
        "MAGISTERIUM_MCP_ENABLED": "false",
        "AUDIT_SINK": "file",
        "FEATURE_ATHENA_LABS": "true",
        "ATHENA_ENV": "preview",
        "ATHENA_PRACTICE_ID": "195900",
        "LABS_ALLOW_PATIENT_SEARCH": "false",
    },
    "public": {
        "MOCK_MODE": "false",
        "SOVEREIGN_MODE": "hybrid",
        "LLM_PROVIDER": "azure_openai",
        "MAGISTERIUM_MCP_ENABLED": "false",
        "AUDIT_SINK": "file",
    },
}

URL_KEYS = {
    "OIDC_ISSUER", "OIDC_JWKS_URL", "AZURE_OPENAI_ENDPOINT",
    "MAGISTERIUM_BASE_URL", "NPPES_BASE_URL",
    "CLINICALTRIALS_BASE_URL",
}
PLACEHOLDERS = ("replace_me", "your-instance", ".example", "<", ">")
OPAQUE_RE = re.compile(r"^[A-Za-z0-9._~-]{8,160}$")


class ConfigError(ValueError):
    """A non-secret deployment invariant is missing or unsafe."""


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_env(path: Path) -> dict[str, str]:
    """Parse simple KEY=value lines and reject ambiguous duplicate entries."""
    if not path.is_file():
        raise ConfigError("deployment env file does not exist")
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            raise ConfigError(f"line {number} is not a KEY=value assignment")
        name, value = line.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise ConfigError(f"line {number} has an invalid key name")
        if name in values:
            raise ConfigError(f"duplicate deployment key: {name}")
        values[name] = _unquote(value.strip())
    return values


def _exact_keys(values: dict[str, str], expected: set[str]) -> None:
    missing = sorted(expected - values.keys())
    extra = sorted(values.keys() - expected)
    if missing:
        raise ConfigError("missing deployment keys: " + ", ".join(missing))
    if extra:
        raise ConfigError("unexpected deployment keys: " + ", ".join(extra))


def _https_url(name: str, value: str) -> None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ConfigError(f"{name} must be an exact credential-free HTTPS URL") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "*" in value
        or (port is not None and not 1 <= port <= 65_535)
    ):
        raise ConfigError(f"{name} must be an exact credential-free HTTPS URL")


def _cors(value: str) -> None:
    try:
        origins = json.loads(value)
    except json.JSONDecodeError:
        raise ConfigError("CORS_ALLOW_ORIGINS must be a JSON list") from None
    if not isinstance(origins, list) or not origins or len(origins) != len(set(origins)):
        raise ConfigError("CORS_ALLOW_ORIGINS must contain unique HTTPS origins")
    for origin in origins:
        if not isinstance(origin, str):
            raise ConfigError("CORS_ALLOW_ORIGINS entries must be strings")
        try:
            parsed = urlsplit(origin)
            port = parsed.port
        except ValueError:
            raise ConfigError(
                "CORS_ALLOW_ORIGINS must contain exact HTTPS origins"
            ) from None
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or "*" in origin
            or (port is not None and not 1 <= port <= 65_535)
        ):
            raise ConfigError("CORS_ALLOW_ORIGINS must contain exact HTTPS origins")


def validate(environment: str, mount: str, values: dict[str, str]) -> None:
    if environment not in ONEPASSWORD_VAULTS:
        raise ConfigError("environment must be prod or staging")
    if mount == "provider":
        public_keys, secret_keys = PROVIDER_PUBLIC, PROVIDER_SECRETS
    elif mount == "public":
        public_keys, secret_keys = PUBLIC_PUBLIC, PUBLIC_SECRETS
    else:
        raise ConfigError("mount must be provider or public")

    _exact_keys(values, public_keys | secret_keys)
    for name in public_keys:
        value = values[name]
        if not value:
            raise ConfigError(f"{name} must not be empty")
        if value.startswith("op://"):
            raise ConfigError(f"{name} must not contain a secret reference")
    for name in secret_keys:
        if not values[name].startswith("op://") or len(values[name]) <= len("op://"):
            raise ConfigError(f"{name} must be a non-empty op:// reference")
    vault = ONEPASSWORD_VAULTS[environment]
    for name, item in SECRET_ITEMS[mount].items():
        expected = f"op://{vault}/{item}"
        if values[name] != expected:
            raise ConfigError(
                f"{name} must use the exact {environment} {mount} 1Password reference"
            )

    for name, expected in FIXED[mount].items():
        if values[name] != expected:
            raise ConfigError(f"{name} must be {expected!r} for the {mount} mount")
    if environment == "staging" and mount == "provider":
        for name, expected in STAGING_PROVIDER_AUTH.items():
            if values[name] != expected:
                raise ConfigError(
                    f"{name} must match the accepted staging Clerk contract"
                )
    for name in URL_KEYS & values.keys():
        _https_url(name, values[name])
    _cors(values["CORS_ALLOW_ORIGINS"])

    for name in public_keys:
        if any(marker in values[name].casefold() for marker in PLACEHOLDERS):
            raise ConfigError(f"{name} still contains a placeholder")

    if mount == "provider":
        if values["OIDC_ALGORITHMS"] != '["RS256"]':
            raise ConfigError("OIDC_ALGORITHMS must be exactly [\"RS256\"]")
        issuer = urlsplit(values["OIDC_ISSUER"])
        jwks = urlsplit(values["OIDC_JWKS_URL"])
        if issuer.hostname != jwks.hostname:
            raise ConfigError("OIDC_ISSUER and OIDC_JWKS_URL must use the same host")
        if not OPAQUE_RE.fullmatch(values["MOUNT_TENANT_ID"]):
            raise ConfigError("MOUNT_TENANT_ID must be an opaque reference")
    else:
        integer_limits = {
            "PUBLIC_CHAT_RATE_LIMIT_REQUESTS": (1, 10_000),
            "PUBLIC_CHAT_RATE_LIMIT_MAX_CLIENTS": (100, 1_000_000),
        }
        for name, (minimum, maximum) in integer_limits.items():
            if not re.fullmatch(r"[0-9]+", values[name]):
                raise ConfigError(f"{name} must be a base-10 integer")
            number = int(values[name])
            if not minimum <= number <= maximum:
                raise ConfigError(f"{name} is outside the runtime bounds")
        try:
            window = float(values["PUBLIC_CHAT_RATE_LIMIT_WINDOW_SECONDS"])
        except ValueError:
            raise ConfigError(
                "PUBLIC_CHAT_RATE_LIMIT_WINDOW_SECONDS must be numeric"
            ) from None
        if not math.isfinite(window) or not 0 < window <= 86_400:
            raise ConfigError(
                "PUBLIC_CHAT_RATE_LIMIT_WINDOW_SECONDS is outside the runtime bounds"
            )


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(
            "usage: validate-deployment-env.py <prod|staging> <provider|public> <env-file>",
            file=sys.stderr,
        )
        return 2
    environment, mount, path = argv[1], argv[2], Path(argv[3])
    try:
        validate(environment, mount, parse_env(path))
    except (ConfigError, OSError) as error:
        print(f"deployment config invalid: {error}", file=sys.stderr)
        return 1
    print(f"validated {mount} deployment configuration")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
