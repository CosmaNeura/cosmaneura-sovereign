#!/usr/bin/env python3
"""Admit one exact Session Prep source pair as a staging build input.

The admission step reuses the reviewed source-pair reconciler, including its
fresh UI receipt verification.  It performs no network request, image build,
registry write, deployment, or readiness promotion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, NoReturn, Sequence

from tools import reconcile_session_prep_source_candidate as source_candidate


SCHEMA = "cosmaneura.session-prep.staging-build-admission.v1"
MAX_OUTPUT_BYTES = 8 * 1024
STAGING_REGISTRY = "cosmaneurasovereignstagingregistry.azurecr.io"
IMAGE_REPOSITORY = "cosmaneura/sovereign"
SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
FAIL_SUMMARY = "[FAIL] Session Prep staging build admission rejected."

DEFERRED_CLAIMS = (
    "image_built",
    "staging_deployed",
    "staging_validated",
    "auth_iam_ready",
    "durable_operational_controls_ready",
    "real_provider_phi_allowed",
    "production_ready",
)


class AdmissionError(RuntimeError):
    """The source pair or bounded staging-build evidence failed closed."""


def _fail(message: str) -> NoReturn:
    raise AdmissionError(message)


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        _fail("The staging build evidence is not canonical JSON")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _require_exact_mapping(
    value: object,
    expected_keys: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected_keys:
        _fail(f"{label} has an unexpected shape")
    return value


def _validate_source_packet(packet: object) -> dict[str, Any]:
    envelope = _require_exact_mapping(
        packet,
        {"evidence", "evidence_sha256"},
        "The source-pair packet",
    )
    evidence = envelope["evidence"]
    if not isinstance(evidence, dict):
        _fail("The source-pair evidence is not an object")
    canonical = _canonical_json(evidence).encode("ascii")
    if envelope["evidence_sha256"] != _sha256(canonical):
        _fail("The source-pair evidence digest does not match")
    if evidence.get("schema_version") != source_candidate.SCHEMA:
        _fail("The source-pair evidence schema is not accepted")
    if evidence.get("source_pair_reconciled") is not True:
        _fail("The source pair was not reconciled")
    for claim in DEFERRED_CLAIMS:
        if evidence.get(claim) is not False:
            _fail("A deferred readiness claim was promoted")

    core = _require_exact_mapping(
        evidence.get("core"),
        {
            "accepted_base_commit",
            "candidate_commit",
            "candidate_tree",
            "lineage_verified",
        },
        "The Core source evidence",
    )
    sovereign = _require_exact_mapping(
        evidence.get("sovereign"),
        {
            "accepted_base_commit",
            "candidate_commit",
            "candidate_tree",
            "lineage_verified",
        },
        "The Sovereign source evidence",
    )
    if core != {
        "accepted_base_commit": source_candidate.ACCEPTED_CORE_COMMIT,
        "candidate_commit": source_candidate.CANDIDATE_CORE_COMMIT,
        "candidate_tree": source_candidate.CANDIDATE_CORE_TREE,
        "lineage_verified": True,
    }:
        _fail("The Core build source is not the reviewed exact candidate")
    if sovereign != {
        "accepted_base_commit": source_candidate.ACCEPTED_SOVEREIGN_COMMIT,
        "candidate_commit": source_candidate.CANDIDATE_SOVEREIGN_COMMIT,
        "candidate_tree": source_candidate.CANDIDATE_SOVEREIGN_TREE,
        "lineage_verified": True,
    }:
        _fail("The Sovereign build source is not the reviewed exact candidate")

    compatibility = _require_exact_mapping(
        evidence.get("compatibility"),
        {"contracts", "controls", "panel"},
        "The source compatibility evidence",
    )
    contracts = _require_exact_mapping(
        compatibility.get("contracts"),
        {"byte_compatible", "file_count", "manifest_sha256"},
        "The contract compatibility evidence",
    )
    controls = _require_exact_mapping(
        compatibility.get("controls"),
        {
            "byte_compatible",
            "core_runtime_default_off",
            "file_count",
            "manifest_sha256",
            "sovereign_runtime_default_off",
            "staging_feature_forced_off",
        },
        "The feature-control evidence",
    )
    panel = _require_exact_mapping(
        compatibility.get("panel"),
        {"byte_compatible", "sha256", "sri"},
        "The panel compatibility evidence",
    )
    if (
        contracts.get("byte_compatible") is not True
        or contracts.get("file_count") != len(source_candidate.CONTRACT_PATHS)
        or not isinstance(contracts.get("manifest_sha256"), str)
        or SHA256_PATTERN.fullmatch(contracts["manifest_sha256"]) is None
        or controls.get("byte_compatible") is not True
        or controls.get("core_runtime_default_off") is not True
        or controls.get("sovereign_runtime_default_off") is not True
        or controls.get("staging_feature_forced_off") is not True
        or controls.get("file_count")
        != len(source_candidate.CORE_RUNTIME_PATHS)
        + len(source_candidate.SOVEREIGN_RUNTIME_PATHS)
        or not isinstance(controls.get("manifest_sha256"), str)
        or SHA256_PATTERN.fullmatch(controls["manifest_sha256"]) is None
        or panel
        != {
            "byte_compatible": True,
            "sha256": "sha256:" + source_candidate.PANEL_SHA256,
            "sri": source_candidate.PANEL_SRI,
        }
    ):
        _fail("The Session Prep compatibility evidence is not fail-closed")

    attestation = _require_exact_mapping(
        evidence.get("ui_operator_attestation"),
        {
            "existing_verifier_validated",
            "operator_observation_proven",
            "authority",
            "receipt_file_sha256",
        },
        "The UI receipt evidence",
    )
    if (
        attestation.get("existing_verifier_validated") is not True
        or attestation.get("operator_observation_proven") is not False
        or attestation.get("authority")
        != "closed-metadata-and-pinned-automated-support-only"
        or not isinstance(attestation.get("receipt_file_sha256"), str)
        or SHA256_PATTERN.fullmatch(attestation["receipt_file_sha256"]) is None
    ):
        _fail("The UI receipt evidence is not the accepted bounded attestation")
    return evidence


def _validate_public_evidence(evidence: dict[str, Any]) -> None:
    if evidence.get("build_input_admitted") is not True:
        _fail("The exact build input was not admitted")
    if evidence.get("source_pair_reconciled") is not True:
        _fail("The exact source pair was not retained")
    for claim in DEFERRED_CLAIMS:
        if evidence.get(claim) is not False:
            _fail("A deferred readiness claim was promoted")
    serialized = _canonical_json(evidence)
    if len(serialized.encode("ascii")) > MAX_OUTPUT_BYTES:
        _fail("The staging build admission exceeds its output bound")
    forbidden = (
        "authorization",
        "bearer ",
        "client_secret",
        "api_key",
        "private_key",
        "patient_name",
        "provider_identifier",
        os.fspath(Path.home()),
    )
    if any(item and item.casefold() in serialized.casefold() for item in forbidden):
        _fail("The staging build admission violates the metadata allowlist")
    if re.search(r"(?<![A-Za-z0-9])/(?:Users|home|private|tmp|var)/", serialized):
        _fail("The staging build admission contains an absolute filesystem path")


def admit(
    *,
    core_repo: Path,
    sovereign_repo: Path,
    ui_receipt: Path,
) -> dict[str, Any]:
    """Return bounded admission metadata for the one reviewed source pair."""
    source_packet = source_candidate.reconcile(
        core_repo=core_repo,
        core_commit=source_candidate.CANDIDATE_CORE_COMMIT,
        sovereign_repo=sovereign_repo,
        sovereign_commit=source_candidate.CANDIDATE_SOVEREIGN_COMMIT,
        ui_receipt=ui_receipt,
    )
    source = _validate_source_packet(source_packet)
    receipt = source["ui_operator_attestation"]
    evidence: dict[str, Any] = {
        "schema_version": SCHEMA,
        "environment": "staging",
        "registry": STAGING_REGISTRY,
        "repository": IMAGE_REPOSITORY,
        "source_pair_reconciled": True,
        "build_input_admitted": True,
        "source_pair_evidence_sha256": source_packet["evidence_sha256"],
        "core": {
            "candidate_commit": source_candidate.CANDIDATE_CORE_COMMIT,
            "candidate_tree": source_candidate.CANDIDATE_CORE_TREE,
        },
        "sovereign": {
            "candidate_commit": source_candidate.CANDIDATE_SOVEREIGN_COMMIT,
            "candidate_tree": source_candidate.CANDIDATE_SOVEREIGN_TREE,
        },
        "ui_operator_attestation": {
            "existing_verifier_validated": True,
            "operator_observation_proven": False,
            "receipt_file_sha256": receipt["receipt_file_sha256"],
        },
        "image_built": False,
        "staging_deployed": False,
        "staging_validated": False,
        "auth_iam_ready": False,
        "durable_operational_controls_ready": False,
        "real_provider_phi_allowed": False,
        "production_ready": False,
    }
    _validate_public_evidence(evidence)
    canonical = _canonical_json(evidence)
    return {
        "evidence": evidence,
        "evidence_sha256": _sha256(canonical.encode("ascii")),
    }


def build_fields(packet: dict[str, Any]) -> str:
    """Return the three fixed, shell-safe fields consumed by the build wrapper."""
    envelope = _require_exact_mapping(
        packet,
        {"evidence", "evidence_sha256"},
        "The build-admission packet",
    )
    evidence = envelope["evidence"]
    if not isinstance(evidence, dict):
        _fail("The build-admission evidence is not an object")
    _validate_public_evidence(evidence)
    sovereign = _require_exact_mapping(
        evidence.get("sovereign"),
        {"candidate_commit", "candidate_tree"},
        "The Sovereign build source",
    )
    commit = sovereign.get("candidate_commit")
    tree = sovereign.get("candidate_tree")
    digest = envelope.get("evidence_sha256")
    if (
        commit != source_candidate.CANDIDATE_SOVEREIGN_COMMIT
        or tree != source_candidate.CANDIDATE_SOVEREIGN_TREE
        or not isinstance(commit, str)
        or COMMIT_PATTERN.fullmatch(commit) is None
        or not isinstance(tree, str)
        or COMMIT_PATTERN.fullmatch(tree) is None
        or not isinstance(digest, str)
        or SHA256_PATTERN.fullmatch(digest) is None
    ):
        _fail("The build-admission fields are not exact")
    canonical = _canonical_json(evidence).encode("ascii")
    if digest != _sha256(canonical):
        _fail("The build-admission evidence digest does not match")
    fields = "\t".join((commit, tree, digest))
    if len(fields.encode("ascii")) > 256:
        _fail("The build-admission fields exceed their output bound")
    return fields


class _SanitizedArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        _fail("The command invocation is invalid")


def _parser() -> argparse.ArgumentParser:
    parser = _SanitizedArgumentParser(
        description="Admit the exact synthetic-only Session Prep staging build input."
    )
    parser.add_argument("--core-repo", required=True, type=Path)
    parser.add_argument("--sovereign-repo", default=source_candidate.ROOT, type=Path)
    parser.add_argument("--ui-receipt", required=True, type=Path)
    parser.add_argument(
        "--output",
        choices=("json", "build-fields"),
        default="json",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        packet = admit(
            core_repo=arguments.core_repo,
            sovereign_repo=arguments.sovereign_repo,
            ui_receipt=arguments.ui_receipt,
        )
        output = (
            build_fields(packet)
            if arguments.output == "build-fields"
            else _canonical_json(packet)
        )
        maximum = 256 if arguments.output == "build-fields" else MAX_OUTPUT_BYTES
        if not output or len(output.encode("ascii")) > maximum:
            _fail("The staging build admission output is invalid")
    except Exception:
        print(FAIL_SUMMARY, file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
