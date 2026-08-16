"""Environment-driven configuration.

Secrets come from environment variables (locally via .env; in Azure, inject
from Key Vault via managed identity — never bake secrets into images/repos).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# NOTE (vendoring delta): the delivered file called load_dotenv() at import
# time. This service loads .env through pydantic-settings, so that second
# config system is dropped — see app/clients/athena/__init__.py.

BASE_URLS = {
    "preview": "https://api.preview.platform.athenahealth.com",
    "production": "https://api.platform.athenahealth.com",
}


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    env: str = "preview"
    practice_id: str = "195900"
    department_id: str = "1"

    @property
    def base_url(self) -> str:
        return BASE_URLS[self.env]

    @property
    def fhir_base(self) -> str:
        return f"{self.base_url}/fhir/r4"

    @property
    def token_url(self) -> str:
        return f"{self.base_url}/oauth2/v1/token"

    @property
    def ah_practice(self) -> str:
        """athenahealth FHIR practice-scoping token (ah-practice search param)."""
        return f"Organization/a-1.Practice-{self.practice_id}"


def load_settings() -> Settings:
    env = os.environ.get("ATHENA_ENV", "preview").lower()
    if env not in BASE_URLS:
        raise ValueError(f"ATHENA_ENV must be one of {list(BASE_URLS)}, got {env!r}")
    client_id = os.environ.get("ATHENA_CLIENT_ID", "")
    client_secret = os.environ.get("ATHENA_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError(
            "Set ATHENA_CLIENT_ID and ATHENA_CLIENT_SECRET (see .env.example). "
            "You get these when you create an App in the athenahealth Developer Console."
        )
    return Settings(
        client_id=client_id,
        client_secret=client_secret,
        env=env,
        practice_id=os.environ.get("ATHENA_PRACTICE_ID", "195900"),
        department_id=os.environ.get("ATHENA_DEPARTMENT_ID", "1"),
    )
