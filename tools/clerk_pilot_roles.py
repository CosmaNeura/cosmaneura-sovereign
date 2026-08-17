"""Dry-run-first operator for the narrow Clerk provider-pilot role.

This tool deliberately exposes no command-line option for a Clerk secret. The
secret is read from ``CLERK_SECRET_KEY`` or, with ``--secret-stdin``, from a
non-interactive stdin pipe such as ``op read``. Clerk error bodies and request
URLs are never rendered because they can contain account data.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TextIO
from urllib.parse import quote

import httpx


CLERK_API_BASE = "https://api.clerk.com/v1"
PROVIDER_ROLE = "provider"
RESERVED_ADMIN_EMAIL = "dev@cosmaneura.com"
MAX_BATCH_SIZE = 25
_USER_ID = re.compile(r"user_[A-Za-z0-9]+\Z")
_MISSING = object()


class OperatorError(RuntimeError):
    """A safe-to-render operator or Clerk contract failure."""


class SafeArgumentParser(argparse.ArgumentParser):
    """Avoid echoing malformed argv, which could contain an accidental secret."""

    def error(self, _message: str) -> None:
        raise OperatorError("invalid command line; use --help for supported options")


@dataclass(frozen=True)
class ClerkUser:
    user_id: str
    email: str
    public_metadata: dict[str, Any]
    is_reserved_admin: bool


@dataclass(frozen=True)
class PlanItem:
    user: ClerkUser
    operation: str
    needs_change: bool

    @property
    def description(self) -> str:
        if not self.needs_change:
            state = (
                "already provider" if self.operation == "grant" else "already revoked"
            )
            return f"{self.user.email}: {state}"
        state = (
            "set sovereign_role=provider"
            if self.operation == "grant"
            else "remove sovereign_role=provider"
        )
        return f"{self.user.email}: {state}"


class ClerkApi:
    """Minimal Clerk Backend API client with non-leaking failures."""

    def __init__(
        self,
        secret_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=CLERK_API_BASE,
            headers={
                "Authorization": f"Bearer {secret_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "cosmaneura-clerk-pilot-role-operator/1",
            },
            follow_redirects=False,
            timeout=httpx.Timeout(15.0, connect=10.0),
            transport=transport,
        )

    def __enter__(self) -> ClerkApi:
        return self

    def __exit__(self, *_args: object) -> None:
        self._client.close()

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        params: Sequence[tuple[str, str]] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> Any:
        try:
            response = self._client.request(method, path, params=params, json=json)
        except httpx.RequestError as error:
            raise OperatorError(
                f"Clerk {operation} failed ({error.__class__.__name__})"
            ) from None
        if not 200 <= response.status_code < 300:
            raise OperatorError(
                f"Clerk {operation} failed with HTTP {response.status_code}"
            )
        try:
            return response.json()
        except ValueError:
            raise OperatorError(f"Clerk {operation} returned malformed JSON") from None

    def resolve_exact_user(self, email: str) -> ClerkUser:
        payload = self._request_json(
            "GET",
            "/users",
            operation="user lookup",
            params=(("email_address", email), ("limit", "100")),
        )
        if isinstance(payload, list):
            candidates = payload
        elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
            candidates = payload["data"]
        else:
            raise OperatorError(
                "Clerk user lookup returned an unexpected response shape"
            )

        matches: dict[str, ClerkUser] = {}
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            addresses = candidate.get("email_addresses")
            if not isinstance(addresses, list):
                continue
            normalized_addresses = {
                address["email_address"].strip().casefold()
                for address in addresses
                if isinstance(address, dict)
                and isinstance(address.get("email_address"), str)
            }
            exact_match = email in normalized_addresses
            if not exact_match:
                continue
            user_id = candidate.get("id")
            metadata = candidate.get("public_metadata", {})
            if not isinstance(user_id, str) or not _USER_ID.fullmatch(user_id):
                raise OperatorError(
                    "Clerk user lookup returned an invalid user identifier"
                )
            if not isinstance(metadata, dict):
                raise OperatorError(
                    "Clerk user lookup returned malformed public metadata"
                )
            matches[user_id] = ClerkUser(
                user_id=user_id,
                email=email,
                public_metadata=dict(metadata),
                is_reserved_admin=RESERVED_ADMIN_EMAIL in normalized_addresses,
            )

        if len(matches) != 1:
            raise OperatorError(
                f"expected exactly one existing Clerk user for {email}; found {len(matches)}"
            )
        return next(iter(matches.values()))

    def update_provider_role(self, item: PlanItem) -> None:
        value: str | None = PROVIDER_ROLE if item.operation == "grant" else None
        payload = self._request_json(
            "PATCH",
            f"/users/{quote(item.user.user_id, safe='')}/metadata",
            operation="metadata update",
            json={"public_metadata": {"sovereign_role": value}},
        )
        if not isinstance(payload, dict) or not isinstance(
            payload.get("public_metadata"), dict
        ):
            raise OperatorError(
                "Clerk metadata update returned an unexpected response shape"
            )
        updated_role = payload["public_metadata"].get("sovereign_role", _MISSING)
        if item.operation == "grant" and updated_role != PROVIDER_ROLE:
            raise OperatorError(
                "Clerk metadata update did not confirm the provider role"
            )
        if (
            item.operation == "revoke"
            and updated_role is not _MISSING
            and updated_role is not None
        ):
            raise OperatorError("Clerk metadata update did not confirm role revocation")


def normalize_emails(
    raw_emails: Sequence[str],
    *,
    allow_reserved_admin: bool,
) -> list[str]:
    if not raw_emails:
        raise OperatorError("at least one --email is required")
    if len(raw_emails) > MAX_BATCH_SIZE:
        raise OperatorError(f"pilot batches are limited to {MAX_BATCH_SIZE} users")

    normalized: list[str] = []
    seen: set[str] = set()
    for position, raw in enumerate(raw_emails, start=1):
        email = raw.strip().casefold()
        local, separator, domain = email.partition("@")
        if (
            not separator
            or "@" in domain
            or not local
            or not domain
            or len(email) > 254
            or any(character.isspace() for character in email)
        ):
            raise OperatorError(f"email value {position} is invalid")
        if email in seen:
            raise OperatorError(f"duplicate email in batch: {email}")
        if email == RESERVED_ADMIN_EMAIL and not allow_reserved_admin:
            raise OperatorError(
                f"{RESERVED_ADMIN_EMAIL} is a reserved admin; "
                "use --break-glass-reserved-admin only with explicit approval"
            )
        seen.add(email)
        normalized.append(email)
    return normalized


def load_secret(
    *,
    from_stdin: bool,
    environ: Mapping[str, str],
    stdin: TextIO,
) -> str:
    env_secret = environ.get("CLERK_SECRET_KEY", "")
    if from_stdin:
        if env_secret.strip():
            raise OperatorError(
                "choose either CLERK_SECRET_KEY or --secret-stdin, not both"
            )
        if getattr(stdin, "isatty", lambda: False)():
            raise OperatorError("--secret-stdin requires a non-interactive pipe")
        secret = stdin.read().strip()
    else:
        secret = env_secret.strip()

    if len(secret) < 20 or any(character.isspace() for character in secret):
        raise OperatorError(
            "a valid Clerk secret is required via CLERK_SECRET_KEY or --secret-stdin"
        )
    return secret


def build_plan(
    api: ClerkApi,
    operation: str,
    emails: Sequence[str],
    *,
    allow_reserved_admin: bool,
) -> list[PlanItem]:
    users = [api.resolve_exact_user(email) for email in emails]
    user_ids: set[str] = set()
    plan: list[PlanItem] = []
    for user in users:
        if user.is_reserved_admin and not allow_reserved_admin:
            raise OperatorError(
                f"{RESERVED_ADMIN_EMAIL} is a reserved admin; "
                "use --break-glass-reserved-admin only with explicit approval"
            )
        if user.user_id in user_ids:
            raise OperatorError("two supplied emails resolve to the same Clerk user")
        user_ids.add(user.user_id)

        role = user.public_metadata.get("sovereign_role", _MISSING)
        if role is not _MISSING and role != PROVIDER_ROLE:
            raise OperatorError(
                f"{user.email} has a different sovereign_role; refusing to overwrite it"
            )
        needs_change = (
            role is _MISSING if operation == "grant" else role == PROVIDER_ROLE
        )
        plan.append(PlanItem(user=user, operation=operation, needs_change=needs_change))
    return plan


def execute(
    api: ClerkApi,
    *,
    operation: str,
    emails: Sequence[str],
    apply: bool,
    allow_reserved_admin: bool,
    emit: Callable[[str], None],
) -> int:
    plan = build_plan(
        api,
        operation,
        emails,
        allow_reserved_admin=allow_reserved_admin,
    )
    mode = "APPLY" if apply else "DRY-RUN"
    emit(f"{mode}: {operation} provider role for {len(plan)} Clerk user(s)")
    for item in plan:
        emit(f"- {item.description}")

    if not apply:
        emit(
            "No Clerk metadata changed. Review this exact list, then repeat with --apply."
        )
        return 0

    changed = 0
    for item in plan:
        if not item.needs_change:
            continue
        try:
            api.update_provider_role(item)
        except OperatorError as error:
            raise OperatorError(
                f"apply stopped after {changed} successful change(s): {error}"
            ) from None
        changed += 1
    emit(
        f"Applied {changed} change(s); {len(plan) - changed} already in requested state."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(
        description=(
            "Dry-run-first Clerk operator for the controlled Sovereign provider pilot. "
            "Secrets are accepted only through the environment or a stdin pipe."
        )
    )
    parser.add_argument("operation", choices=("grant", "revoke"))
    parser.add_argument(
        "--email",
        action="append",
        required=True,
        help="Exact existing Clerk email; repeat once per approved pilot user.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the reviewed plan. Omit for the default dry-run.",
    )
    parser.add_argument(
        "--secret-stdin",
        action="store_true",
        help="Read the Clerk secret from a non-interactive stdin pipe instead of the environment.",
    )
    parser.add_argument(
        "--break-glass-reserved-admin",
        action="store_true",
        help=f"Permit the reserved {RESERVED_ADMIN_EMAIL} account after explicit approval.",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    transport: httpx.BaseTransport | None = None,
) -> int:
    environ = os.environ if environ is None else environ
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    try:
        args = build_parser().parse_args(argv)
        emails = normalize_emails(
            args.email,
            allow_reserved_admin=args.break_glass_reserved_admin,
        )
        secret = load_secret(
            from_stdin=args.secret_stdin,
            environ=environ,
            stdin=stdin,
        )
        with ClerkApi(secret, transport=transport) as api:
            return execute(
                api,
                operation=args.operation,
                emails=emails,
                apply=args.apply,
                allow_reserved_admin=args.break_glass_reserved_admin,
                emit=lambda line: print(line, file=stdout),
            )
    except OperatorError as error:
        print(f"ERROR: {error}", file=stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
