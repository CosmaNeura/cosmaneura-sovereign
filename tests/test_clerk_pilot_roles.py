"""Offline safety and API-contract tests for the Clerk pilot-role operator."""

from __future__ import annotations

import json
from io import StringIO
from typing import Any

import httpx
import pytest

from tools import clerk_pilot_roles


SECRET = "synthetic-clerk-secret-" + "s" * 32


def _user(
    user_id: str,
    email: str,
    *,
    metadata: dict[str, Any] | None = None,
    additional_emails: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "id": user_id,
        "email_addresses": [
            {"email_address": address} for address in (email, *additional_emails)
        ],
        "public_metadata": {} if metadata is None else metadata,
    }


def _run(
    argv: list[str],
    handler: httpx.MockTransport,
    *,
    environ: dict[str, str] | None = None,
    stdin: StringIO | None = None,
) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    result = clerk_pilot_roles.main(
        argv,
        environ={"CLERK_SECRET_KEY": SECRET} if environ is None else environ,
        stdin=StringIO() if stdin is None else stdin,
        stdout=stdout,
        stderr=stderr,
        transport=handler,
    )
    return result, stdout.getvalue(), stderr.getvalue()


def test_default_is_dry_run_and_does_not_patch_or_render_metadata() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        assert request.url.params["email_address"] == "john@cosmaneura.com"
        return httpx.Response(
            200,
            json=[
                _user(
                    "user_John1",
                    "john@cosmaneura.com",
                    metadata={"department": "sentinel-private-metadata"},
                )
            ],
        )

    result, stdout, stderr = _run(
        ["grant", "--email", "John@CosmaNeura.com"],
        httpx.MockTransport(handler),
    )

    assert result == 0
    assert stderr == ""
    assert [request.method for request in requests] == ["GET"]
    assert "DRY-RUN" in stdout
    assert "set sovereign_role=provider" in stdout
    assert "No Clerk metadata changed" in stdout
    assert SECRET not in stdout
    assert "sentinel-private-metadata" not in stdout


def test_apply_grant_patches_only_sovereign_role_and_preserves_other_metadata() -> None:
    metadata = {"department": "clinical", "pilot_number": 7}
    patch_body: dict[str, Any] | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal patch_body
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[_user("user_John1", "john@cosmaneura.com", metadata=metadata)],
            )
        assert request.method == "PATCH"
        assert request.url.path == "/v1/users/user_John1/metadata"
        patch_body = json.loads(request.content)
        return httpx.Response(
            200,
            json={"public_metadata": {**metadata, "sovereign_role": "provider"}},
        )

    result, stdout, stderr = _run(
        ["grant", "--email", "john@cosmaneura.com", "--apply"],
        httpx.MockTransport(handler),
    )

    assert result == 0
    assert stderr == ""
    assert patch_body == {"public_metadata": {"sovereign_role": "provider"}}
    assert "Applied 1 change(s)" in stdout


def test_apply_revoke_deletes_only_the_provider_role_key() -> None:
    metadata = {"department": "clinical", "sovereign_role": "provider"}
    patch_body: dict[str, Any] | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal patch_body
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[_user("user_John1", "john@cosmaneura.com", metadata=metadata)],
            )
        patch_body = json.loads(request.content)
        return httpx.Response(200, json={"public_metadata": {"department": "clinical"}})

    result, stdout, stderr = _run(
        ["revoke", "--email", "john@cosmaneura.com", "--apply"],
        httpx.MockTransport(handler),
    )

    assert result == 0
    assert stderr == ""
    assert patch_body == {"public_metadata": {"sovereign_role": None}}
    assert "remove sovereign_role=provider" in stdout


@pytest.mark.parametrize(
    ("operation", "metadata"),
    [
        ("grant", {"sovereign_role": "provider", "department": "clinical"}),
        ("revoke", {"department": "clinical"}),
    ],
)
def test_apply_is_idempotent_when_role_is_already_in_requested_state(
    operation: str,
    metadata: dict[str, Any],
) -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(
            200,
            json=[_user("user_John1", "john@cosmaneura.com", metadata=metadata)],
        )

    result, stdout, stderr = _run(
        [operation, "--email", "john@cosmaneura.com", "--apply"],
        httpx.MockTransport(handler),
    )

    assert result == 0
    assert stderr == ""
    assert methods == ["GET"]
    assert "Applied 0 change(s)" in stdout


def test_all_users_are_preflighted_before_any_apply() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        email = request.url.params["email_address"]
        if email == "approved@cosmaneura.com":
            return httpx.Response(200, json=[_user("user_Approved1", email)])
        return httpx.Response(200, json=[])

    result, stdout, stderr = _run(
        [
            "grant",
            "--email",
            "approved@cosmaneura.com",
            "--email",
            "missing@cosmaneura.com",
            "--apply",
        ],
        httpx.MockTransport(handler),
    )

    assert result == 2
    assert stdout == ""
    assert methods == ["GET", "GET"]
    assert "found 0" in stderr


def test_exactly_one_matching_user_is_required() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                _user("user_One1", "pilot@cosmaneura.com"),
                _user("user_Two2", "pilot@cosmaneura.com"),
            ],
        )

    result, stdout, stderr = _run(
        ["grant", "--email", "pilot@cosmaneura.com"],
        httpx.MockTransport(handler),
    )

    assert result == 2
    assert stdout == ""
    assert "found 2" in stderr


@pytest.mark.parametrize("operation", ["grant", "revoke"])
def test_different_existing_sovereign_role_is_never_overwritten(operation: str) -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(
            200,
            json=[
                _user(
                    "user_Admin1",
                    "admin@cosmaneura.com",
                    metadata={"sovereign_role": "admin"},
                )
            ],
        )

    result, stdout, stderr = _run(
        [operation, "--email", "admin@cosmaneura.com", "--apply"],
        httpx.MockTransport(handler),
    )

    assert result == 2
    assert stdout == ""
    assert methods == ["GET"]
    assert "refusing to overwrite" in stderr
    assert 'admin"' not in stderr


def test_two_emails_resolving_to_one_user_are_rejected_before_patch() -> None:
    methods: list[str] = []
    user = _user(
        "user_Shared1",
        "first@cosmaneura.com",
        additional_emails=("second@cosmaneura.com",),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(200, json=[user])

    result, stdout, stderr = _run(
        [
            "grant",
            "--email",
            "first@cosmaneura.com",
            "--email",
            "second@cosmaneura.com",
            "--apply",
        ],
        httpx.MockTransport(handler),
    )

    assert result == 2
    assert stdout == ""
    assert methods == ["GET", "GET"]
    assert "same Clerk user" in stderr


def test_reserved_admin_is_rejected_before_secret_or_network_access() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        raise AssertionError("network must not be reached")

    result, stdout, stderr = _run(
        ["grant", "--email", "DEV@COSMANEURA.COM"],
        httpx.MockTransport(handler),
        environ={},
    )

    assert result == 2
    assert stdout == ""
    assert not called
    assert "reserved admin" in stderr
    assert "break-glass" in stderr


def test_reserved_admin_requires_and_accepts_explicit_break_glass() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_user("user_Dev1", "dev@cosmaneura.com")])

    result, stdout, stderr = _run(
        [
            "grant",
            "--email",
            "dev@cosmaneura.com",
            "--break-glass-reserved-admin",
        ],
        httpx.MockTransport(handler),
    )

    assert result == 0
    assert stderr == ""
    assert "DRY-RUN" in stdout


def test_reserved_admin_secondary_email_cannot_bypass_guard() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(
            200,
            json=[
                _user(
                    "user_Dev1",
                    "dev@cosmaneura.com",
                    additional_emails=("dev-pilot-alias@cosmaneura.com",),
                )
            ],
        )

    result, stdout, stderr = _run(
        [
            "grant",
            "--email",
            "dev-pilot-alias@cosmaneura.com",
            "--apply",
        ],
        httpx.MockTransport(handler),
    )

    assert result == 2
    assert stdout == ""
    assert methods == ["GET"]
    assert "reserved admin" in stderr
    assert "break-glass" in stderr


def test_secret_can_arrive_on_stdin_and_is_never_rendered() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        return httpx.Response(200, json=[_user("user_John1", "john@cosmaneura.com")])

    result, stdout, stderr = _run(
        ["grant", "--email", "john@cosmaneura.com", "--secret-stdin"],
        httpx.MockTransport(handler),
        environ={},
        stdin=StringIO(SECRET + "\n"),
    )

    assert result == 0
    assert stderr == ""
    assert SECRET not in stdout


def test_ambiguous_secret_sources_fail_before_network() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        raise AssertionError("network must not be reached")

    result, stdout, stderr = _run(
        ["grant", "--email", "john@cosmaneura.com", "--secret-stdin"],
        httpx.MockTransport(handler),
        stdin=StringIO(SECRET),
    )

    assert result == 2
    assert stdout == ""
    assert not called
    assert SECRET not in stderr
    assert "either" in stderr


def test_malformed_argv_is_not_echoed() -> None:
    accidental_secret = "accidental_cli_secret_that_must_not_be_echoed"
    result, stdout, stderr = _run(
        [
            "grant",
            "--email",
            "john@cosmaneura.com",
            "--secret-key",
            accidental_secret,
        ],
        httpx.MockTransport(lambda _request: httpx.Response(500)),
        environ={},
    )

    assert result == 2
    assert stdout == ""
    assert accidental_secret not in stderr
    assert "invalid command line" in stderr


def test_clerk_failure_body_and_secret_are_sanitized() -> None:
    sentinel = "upstream-secret-body-sentinel"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"{sentinel} {SECRET}")

    result, stdout, stderr = _run(
        ["grant", "--email", "john@cosmaneura.com"],
        httpx.MockTransport(handler),
    )

    assert result == 2
    assert stdout == ""
    assert "HTTP 401" in stderr
    assert sentinel not in stderr
    assert SECRET not in stderr


def test_apply_failure_is_sanitized_and_reports_partial_progress() -> None:
    sentinel = "metadata-failure-body-sentinel"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[_user("user_John1", "john@cosmaneura.com")],
            )
        return httpx.Response(503, text=f"{sentinel} {SECRET}")

    result, stdout, stderr = _run(
        ["grant", "--email", "john@cosmaneura.com", "--apply"],
        httpx.MockTransport(handler),
    )

    assert result == 2
    assert "APPLY" in stdout
    assert "stopped after 0 successful change(s)" in stderr
    assert "HTTP 503" in stderr
    assert sentinel not in stderr
    assert SECRET not in stderr


def test_repeatable_email_option_supports_ten_user_batch() -> None:
    expected = {f"pilot{index}@cosmaneura.com" for index in range(10)}

    def handler(request: httpx.Request) -> httpx.Response:
        email = request.url.params["email_address"]
        assert email in expected
        index = email.removeprefix("pilot").split("@", 1)[0]
        return httpx.Response(200, json=[_user(f"user_Pilot{index}", email)])

    argv = ["grant"]
    for email in sorted(expected):
        argv.extend(("--email", email))
    result, stdout, stderr = _run(argv, httpx.MockTransport(handler))

    assert result == 0
    assert stderr == ""
    assert "10 Clerk user(s)" in stdout
    assert stdout.count("set sovereign_role=provider") == 10
