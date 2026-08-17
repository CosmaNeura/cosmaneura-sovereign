#!/usr/bin/env python3
"""Fail closed unless an immutable ACR image was built from the expected commit."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any


MANIFEST_MEDIA_TYPES = {
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
}
INDEX_MEDIA_TYPES = {
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.index.v1+json",
}
MANIFEST_ACCEPT = ", ".join(sorted(MANIFEST_MEDIA_TYPES | INDEX_MEDIA_TYPES))
MAX_TOKEN_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_CONFIG_BYTES = 8 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 30


class RevisionVerificationError(RuntimeError):
    """The exact image provenance could not be established."""


def _digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _require_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise RevisionVerificationError(f"{label} is not a full sha256 digest")
    return value


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RevisionVerificationError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise RevisionVerificationError(f"{label} must be a JSON object")
    return value


def _verify_bytes_digest(data: bytes, expected: str, label: str) -> None:
    if _digest(data) != expected:
        raise RevisionVerificationError(f"{label} content does not match its digest")


Fetch = Callable[[str, str, int], bytes]


def verify_image_revision(
    repository: str,
    image_digest: str,
    expected_revision: str,
    fetch: Fetch,
) -> None:
    """Verify the config label behind one exact manifest digest.

    A manifest index is accepted only when it selects one unambiguous Linux
    amd64 image. This is the architecture used by the Container Apps mounts.
    Every fetched document is checked against the digest in its parent before
    any label is trusted.
    """

    encoded_repository = urllib.parse.quote(repository, safe="/")
    encoded_image_digest = urllib.parse.quote(image_digest, safe=":")
    root_path = f"/v2/{encoded_repository}/manifests/{encoded_image_digest}"
    manifest_bytes = fetch(root_path, MANIFEST_ACCEPT, MAX_MANIFEST_BYTES)
    _verify_bytes_digest(manifest_bytes, image_digest, "candidate manifest")
    manifest = _json_object(manifest_bytes, "candidate manifest")

    media_type = manifest.get("mediaType")
    if media_type in INDEX_MEDIA_TYPES:
        descriptors = manifest.get("manifests")
        if not isinstance(descriptors, list):
            raise RevisionVerificationError("candidate index has no manifest list")
        matching = []
        for descriptor in descriptors:
            if not isinstance(descriptor, dict):
                continue
            platform = descriptor.get("platform")
            if (
                isinstance(platform, dict)
                and platform.get("os") == "linux"
                and platform.get("architecture") == "amd64"
            ):
                matching.append(descriptor)
        if len(matching) != 1:
            raise RevisionVerificationError(
                "candidate index must contain exactly one linux/amd64 manifest"
            )
        child_digest = _require_digest(
            matching[0].get("digest"), "linux/amd64 manifest digest"
        )
        child_path = (
            f"/v2/{encoded_repository}/manifests/"
            f"{urllib.parse.quote(child_digest, safe=':')}"
        )
        manifest_bytes = fetch(child_path, MANIFEST_ACCEPT, MAX_MANIFEST_BYTES)
        _verify_bytes_digest(manifest_bytes, child_digest, "linux/amd64 manifest")
        manifest = _json_object(manifest_bytes, "linux/amd64 manifest")
        media_type = manifest.get("mediaType")

    if media_type not in MANIFEST_MEDIA_TYPES:
        raise RevisionVerificationError("candidate does not resolve to an OCI image manifest")
    config = manifest.get("config")
    if not isinstance(config, dict):
        raise RevisionVerificationError("candidate manifest has no image config")
    config_digest = _require_digest(config.get("digest"), "image config digest")
    config_path = (
        f"/v2/{encoded_repository}/blobs/"
        f"{urllib.parse.quote(config_digest, safe=':')}"
    )
    config_bytes = fetch(config_path, "application/octet-stream", MAX_CONFIG_BYTES)
    _verify_bytes_digest(config_bytes, config_digest, "image config")
    image_config = _json_object(config_bytes, "image config")
    config_payload = image_config.get("config")
    labels = config_payload.get("Labels") if isinstance(config_payload, dict) else None
    revision = labels.get("org.opencontainers.image.revision") if isinstance(labels, dict) else None
    if revision != expected_revision:
        raise RevisionVerificationError(
            "candidate OCI revision label does not equal the checked-out HEAD"
        )


class _SafeRegistryRedirect(urllib.request.HTTPRedirectHandler):
    """Permit Azure registry/blob redirects without forwarding ACR bearer auth."""

    def __init__(self, registry: str) -> None:
        super().__init__()
        self.registry = registry

    def redirect_request(  # type: ignore[no-untyped-def]
        self, request, file_pointer, code, message, headers, new_url
    ):
        absolute_url = urllib.parse.urljoin(request.full_url, new_url)
        parsed = urllib.parse.urlsplit(absolute_url)
        hostname = (parsed.hostname or "").lower()
        allowed_host = (
            hostname == self.registry
            or hostname.endswith(".blob.core.windows.net")
            or hostname.endswith(".azurecr.io")
        )
        if parsed.scheme != "https" or not allowed_host:
            raise RevisionVerificationError("ACR returned an unsafe redirect")
        redirected = super().redirect_request(
            request, file_pointer, code, message, headers, absolute_url
        )
        if redirected is not None and hostname != self.registry:
            redirected.remove_header("Authorization")
        return redirected


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(  # type: ignore[no-untyped-def]
        self, request, file_pointer, code, message, headers, new_url
    ):
        raise RevisionVerificationError("ACR token endpoint unexpectedly redirected")


def _read_limited(response: Any, limit: int, label: str) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > limit:
                raise RevisionVerificationError(f"{label} exceeded the size limit")
        except ValueError as exc:
            raise RevisionVerificationError(f"{label} returned an invalid size") from exc
    data = response.read(limit + 1)
    if len(data) > limit:
        raise RevisionVerificationError(f"{label} exceeded the size limit")
    return data


def _acr_refresh_token(acr_name: str) -> str:
    try:
        result = subprocess.run(
            [
                "az",
                "acr",
                "login",
                "--name",
                acr_name,
                "--expose-token",
                "--query",
                "accessToken",
                "--output",
                "tsv",
                "--only-show-errors",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RevisionVerificationError("could not obtain a read-only ACR token") from exc
    token = result.stdout.strip()
    if result.returncode != 0 or not token or any(character.isspace() for character in token):
        raise RevisionVerificationError("could not obtain a read-only ACR token")
    return token


def _acr_repository_token(registry: str, repository: str, refresh_token: str) -> str:
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "service": registry,
            "scope": f"repository:{repository}:pull",
            "refresh_token": refresh_token,
        }
    ).encode("ascii")
    request = urllib.request.Request(
        f"https://{registry}/oauth2/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    opener = urllib.request.build_opener(_RejectRedirect())
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = _read_limited(response, MAX_TOKEN_BYTES, "ACR token response")
    except RevisionVerificationError:
        raise
    except urllib.error.HTTPError as exc:
        exc.close()
        raise RevisionVerificationError(
            f"ACR repository token request failed with HTTP {exc.code}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RevisionVerificationError("ACR repository token request failed") from exc
    token_response = _json_object(payload, "ACR token response")
    token = token_response.get("access_token") or token_response.get("token")
    if not isinstance(token, str) or not token or any(character.isspace() for character in token):
        raise RevisionVerificationError("ACR repository token response was invalid")
    return token


def _registry_fetcher(registry: str, access_token: str) -> Fetch:
    opener = urllib.request.build_opener(_SafeRegistryRedirect(registry))

    def fetch(path: str, accept: str, limit: int) -> bytes:
        request = urllib.request.Request(
            f"https://{registry}{path}",
            headers={
                "Accept": accept,
                "Authorization": f"Bearer {access_token}",
            },
            method="GET",
        )
        try:
            with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return _read_limited(response, limit, "ACR artifact")
        except RevisionVerificationError:
            raise
        except urllib.error.HTTPError as exc:
            exc.close()
            raise RevisionVerificationError(
                f"ACR artifact request failed with HTTP {exc.code}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RevisionVerificationError("ACR artifact request failed") from exc

    return fetch


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print(
            "usage: verify-acr-image-revision.py "
            "<registry> <repository> <digest> <expected-revision>",
            file=sys.stderr,
        )
        return 2
    registry, repository, image_digest, expected_revision = argv[1:]
    if not re.fullmatch(r"[a-z0-9]{5,50}\.azurecr\.io", registry):
        print("registry must be one lowercase Azure Container Registry host", file=sys.stderr)
        return 2
    if not re.fullmatch(
        r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*",
        repository,
    ):
        print("repository is invalid", file=sys.stderr)
        return 2
    try:
        _require_digest(image_digest, "candidate image digest")
    except RevisionVerificationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not re.fullmatch(r"[0-9a-f]{40,64}", expected_revision):
        print("expected revision must be a full lowercase Git object ID", file=sys.stderr)
        return 2

    acr_name = registry.removesuffix(".azurecr.io")
    try:
        refresh_token = _acr_refresh_token(acr_name)
        access_token = _acr_repository_token(registry, repository, refresh_token)
        verify_image_revision(
            repository,
            image_digest,
            expected_revision,
            _registry_fetcher(registry, access_token),
        )
    except RevisionVerificationError as exc:
        print(f"candidate provenance verification failed: {exc}", file=sys.stderr)
        return 1
    print("candidate OCI revision label matches the clean checked-out HEAD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
