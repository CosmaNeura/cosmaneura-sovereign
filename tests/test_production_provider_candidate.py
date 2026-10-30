"""Synthetic producer/selection admission; no application, cloud or credentials."""

from __future__ import annotations

import copy
import io
import json
import socket
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
import yaml

from tools import production_provider_candidate as candidate
from tests.test_production_saved_image_identity import fixture as saved_fixture
from tests.test_production_saved_image_identity import save as save_fixture


SHA = "a" * 40
TREE = "b" * 40
RUNTIME = {
    "Labels": {"org.opencontainers.image.revision": SHA},
    "Env": [key + "=" + value for key, value in candidate.DARK_ENV.items()],
}
CONFIG_DIGEST = saved_fixture(RUNTIME, engine="classic")[0]["Id"]
IMAGE_DIGEST = "sha256:" + "d" * 64
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refused(*_args, **_kwargs):
        raise AssertionError("network forbidden in source tests")

    monkeypatch.setattr(socket, "socket", refused)
    monkeypatch.setattr(socket, "create_connection", refused)


def context(run_id=20):
    return {
        "source_sha": SHA,
        "source_tree": TREE,
        "source_archive_digest": candidate.digest(b"synthetic"),
        "build_contract_digest": "sha256:" + "f" * 64,
        "run_id": run_id,
        "run_attempt": 1,
    }


def record():
    return {
        "schema_version": 1,
        "purpose": "verified-production-provider-candidate",
        "repository": candidate.REPOSITORY,
        "workflow_ref": candidate.WORKFLOW_REF,
        **context(10),
        "image": candidate.IMAGE + "@" + IMAGE_DIGEST,
        "image_config_digest": CONFIG_DIGEST,
        "dark_configuration": dict(candidate.DARK_ENV),
        "deployment_authorized": False,
    }


def image_config():
    return saved_fixture(RUNTIME, engine="classic")[0]


def zipped(value=None, *, filename="candidate.json", mode=stat.S_IFREG):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        item = zipfile.ZipInfo(filename)
        item.external_attr = mode << 16
        archive.writestr(
            item, candidate.canonical(record() if value is None else value)
        )
    return output.getvalue()


def evidence():
    raw = zipped()
    run = {
        "id": 10,
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "success",
        "event": "workflow_dispatch",
        "head_branch": "main",
        "head_sha": SHA,
        "repository": {"full_name": candidate.REPOSITORY, "id": 500},
        "actor": {"login": "JohnClem"},
        "triggering_actor": {"login": "JohnClem"},
        "path": candidate.WORKFLOW,
        "workflow_id": 300,
    }
    workflow = {"id": 300, "path": candidate.WORKFLOW, "state": "active"}
    jobs = {
        "total_count": 5,
        "jobs": [
            {
                "id": index + 1,
                "name": name,
                "head_sha": SHA,
                "run_id": 10,
                "run_attempt": 1,
                "status": "completed",
                "conclusion": "skipped"
                if name == "Select provider candidate"
                else "success",
            }
            for index, name in enumerate(sorted(candidate.JOBS))
        ],
    }
    artifact = {
        "id": 100,
        "expired": False,
        "name": f"provider-production-candidate-10-1-{SHA}",
        "size_in_bytes": len(raw),
        "digest": candidate.digest(raw),
        "workflow_run": {
            "id": 10,
            "head_sha": SHA,
            "head_branch": "main",
            "repository_id": 500,
            "head_repository_id": 500,
        },
    }
    return [run, workflow, jobs, artifact, raw]


def verify(values):
    return candidate.verify_evidence(context(), 10, 1, 100, *values)


def test_exact_authenticated_producer_and_archive_is_accepted():
    assert verify(evidence()) == record()


@pytest.mark.parametrize(
    "path,value",
    [
        ("id", 11),
        ("run_attempt", 2),
        ("status", "in_progress"),
        ("conclusion", "failure"),
        ("event", "push"),
        ("head_branch", "other"),
        ("head_sha", "0" * 40),
        ("path", ".github/workflows/build_and_publish.yml"),
        ("workflow_id", 301),
        ("actor.login", "another-user"),
        ("triggering_actor.login", "another-user"),
        ("repository.full_name", "other/repo"),
    ],
)
def test_wrong_producer_cannot_supply_provenance(path, value):
    values = evidence()
    target = values[0]
    pieces = path.split(".")
    for key in pieces[:-1]:
        target = target[key]
    target[pieces[-1]] = value
    with pytest.raises(candidate.CandidateRefused):
        verify(values)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "extra",
        "duplicate",
        "failed",
        "skipped_prepare",
        "select_success",
        "wrong_run",
        "wrong_attempt",
        "wrong_head",
        "incomplete",
        "bad_id",
    ],
)
def test_complete_successful_prepare_job_inventory_is_required(mutation):
    values = evidence()
    jobs = values[2]
    if mutation == "missing":
        jobs["jobs"].pop()
    elif mutation == "extra":
        jobs["jobs"].append(copy.deepcopy(jobs["jobs"][0]))
        jobs["total_count"] += 1
    elif mutation == "duplicate":
        jobs["jobs"][-1]["id"] = jobs["jobs"][0]["id"]
    else:
        job = next(
            item
            for item in jobs["jobs"]
            if item["name"] == "Prepare provider candidate"
        )
        if mutation == "select_success":
            job = next(
                item
                for item in jobs["jobs"]
                if item["name"] == "Select provider candidate"
            )
            job["conclusion"] = "success"
        else:
            key, value = {
                "failed": ("conclusion", "failure"),
                "skipped_prepare": ("conclusion", "skipped"),
                "wrong_run": ("run_id", 11),
                "wrong_attempt": ("run_attempt", 2),
                "wrong_head": ("head_sha", "0" * 40),
                "incomplete": ("status", "in_progress"),
                "bad_id": ("id", True),
            }[mutation]
            job[key] = value
    with pytest.raises(candidate.CandidateRefused):
        verify(values)


@pytest.mark.parametrize(
    "part,key,value",
    [
        (1, "path", ".github/workflows/other.yml"),
        (1, "state", "disabled_manually"),
        (3, "id", 101),
        (3, "expired", True),
        (3, "expired", 0),
        (3, "name", "candidate"),
        (3, "size_in_bytes", 0),
        (3, "size_in_bytes", True),
        (3, "size_in_bytes", candidate.MAX_ARCHIVE + 1),
        (3, "digest", IMAGE_DIGEST),
        (3, "digest", "sha256:" + "A" * 64),
    ],
)
def test_workflow_and_artifact_identity_is_closed(part, key, value):
    values = evidence()
    values[part][key] = value
    with pytest.raises(candidate.CandidateRefused):
        verify(values)


@pytest.mark.parametrize(
    "key,value",
    [
        ("id", 11),
        ("head_sha", "0" * 40),
        ("head_branch", "other"),
        ("repository_id", 501),
        ("head_repository_id", 501),
    ],
)
def test_artifact_must_belong_to_exact_producer_repository_run_and_source(key, value):
    values = evidence()
    values[3]["workflow_run"][key] = value
    with pytest.raises(candidate.CandidateRefused):
        verify(values)


@pytest.mark.parametrize(
    "key,value",
    [
        ("schema_version", True),
        ("schema_version", 2),
        ("purpose", "activation"),
        ("repository", "other/repo"),
        ("workflow_ref", "other/workflow@main"),
        ("source_sha", "0" * 40),
        ("source_tree", "0" * 40),
        ("source_archive_digest", IMAGE_DIGEST),
        ("build_contract_digest", IMAGE_DIGEST),
        ("run_id", 11),
        ("run_attempt", True),
        ("deployment_authorized", True),
        ("dark_configuration", {**candidate.DARK_ENV, "FEATURE_SESSION_PREP": "true"}),
        ("image", candidate.IMAGE + ":latest"),
        (
            "image",
            "cosmaneurasovereignstagingregistry.azurecr.io/cosmaneura/sovereign@"
            + IMAGE_DIGEST,
        ),
        (
            "image",
            candidate.IMAGE.replace("/sovereign", "/public") + "@" + IMAGE_DIGEST,
        ),
        ("image_config_digest", "sha256:" + "A" * 64),
    ],
)
def test_self_hashed_descriptor_and_readiness_cannot_supply_authority(key, value):
    values = evidence()
    modified = record()
    modified[key] = value
    values[4] = zipped(modified)
    values[3]["digest"] = candidate.digest(values[4])
    values[3]["size_in_bytes"] = len(values[4])
    with pytest.raises(candidate.CandidateRefused):
        verify(values)


def test_extra_record_fields_and_same_selection_run_are_refused():
    with pytest.raises(candidate.CandidateRefused):
        candidate.validate_record({**record(), "ready": True}, context(), 10, 1)
    with pytest.raises(candidate.CandidateRefused):
        candidate.verify_evidence(context(10), 10, 1, 100, *evidence())


@pytest.mark.parametrize(
    "filename,mode",
    [
        ("../candidate.json", stat.S_IFREG),
        ("candidate.json", stat.S_IFLNK),
        ("candidate.json", stat.S_IFIFO),
        ("candidate.json", stat.S_IFDIR),
        ("other.json", stat.S_IFREG),
    ],
)
def test_archive_path_and_type_cannot_change(filename, mode):
    raw = zipped(filename=filename, mode=mode)
    with pytest.raises(candidate.CandidateRefused):
        candidate.archive_record(raw, candidate.digest(raw))


def test_extra_zip_member_and_oversized_json_are_refused():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("candidate.json", candidate.canonical(record()))
        archive.writestr("other.json", "{}")
    raw = output.getvalue()
    with pytest.raises(candidate.CandidateRefused):
        candidate.archive_record(raw, candidate.digest(raw))
    raw = zipped({"padding": "a" * candidate.MAX_JSON})
    with pytest.raises(candidate.CandidateRefused):
        candidate.archive_record(raw, candidate.digest(raw))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b"{}" + b" " * candidate.MAX_JSON,
    ],
)
def test_duplicate_nonfinite_or_oversized_document_refused(raw):
    with pytest.raises(candidate.CandidateRefused):
        candidate.decode(raw)


def test_complete_job_metadata_has_separate_one_mib_budget():
    values = evidence()
    values[2]["jobs"][0]["steps"] = [{"name": "synthetic" * 19000}]
    raw = candidate.canonical(values[2])
    assert candidate.MAX_JSON < len(raw) < candidate.MAX_METADATA
    values[2] = candidate.decode(raw, candidate.MAX_METADATA)
    assert verify(values) == record()
    with pytest.raises(candidate.CandidateRefused):
        candidate.decode(raw)


def test_archive_fetch_uses_only_fixed_authenticated_api_coordinates(monkeypatch):
    values = evidence()
    calls = []
    monkeypatch.setattr(candidate, "source_context", context)

    def fake_api(path):
        calls.append(path)
        return values[
            {
                f"repos/{candidate.REPOSITORY}/actions/runs/10": 0,
                f"repos/{candidate.REPOSITORY}/actions/workflows/prepare_provider_candidate.yml": 1,
                f"repos/{candidate.REPOSITORY}/actions/runs/10/attempts/1/jobs?per_page=100": 2,
                f"repos/{candidate.REPOSITORY}/actions/artifacts/100": 3,
            }[path]
        ]

    def fake_command(args, **kwargs):
        assert args == [
            "gh",
            "api",
            "--hostname",
            "github.com",
            f"repos/{candidate.REPOSITORY}/actions/artifacts/100/zip",
        ]
        assert kwargs == {"github": True, "maximum": candidate.MAX_ARCHIVE}
        return values[4]

    values[3]["archive_download_url"] = "https://untrusted.example.invalid/collect"
    monkeypatch.setattr(candidate, "api", fake_api)
    monkeypatch.setattr(candidate, "command", fake_command)
    assert candidate.admit(10, 1, 100) == (record(), values[3]["digest"])
    assert len(calls) == 4


@pytest.mark.parametrize(
    "expired,size",
    [(True, 1), (False, candidate.MAX_ARCHIVE + 1), (False, 0), (False, True)],
)
def test_bad_artifact_metadata_is_refused_before_archive_fetch(
    monkeypatch, expired, size
):
    values = evidence()
    values[3].update(expired=expired, size_in_bytes=size)
    monkeypatch.setattr(candidate, "source_context", context)
    monkeypatch.setattr(
        candidate, "api", lambda path: values[3] if "/artifacts/" in path else {}
    )
    monkeypatch.setattr(
        candidate, "command", lambda *_args, **_kwargs: pytest.fail("archive fetched")
    )
    with pytest.raises(candidate.CandidateRefused):
        candidate.admit(10, 1, 100)


def test_stale_source_after_archive_selection_is_refused(monkeypatch):
    values = evidence()
    calls = iter([context(), {**context(), "source_tree": "0" * 40}])
    monkeypatch.setattr(candidate, "source_context", lambda: next(calls))
    responses = iter(values[:4])
    monkeypatch.setattr(candidate, "api", lambda _path: next(responses))
    monkeypatch.setattr(candidate, "command", lambda *_args, **_kwargs: values[4])
    with pytest.raises(candidate.CandidateRefused):
        candidate.admit(10, 1, 100)


def test_clients_do_not_inherit_build_or_login_credentials(monkeypatch):
    observed = []
    monkeypatch.setenv("GH_TOKEN", "synthetic-github-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-secret")
    monkeypatch.setenv("NIXPACKS_BUILD_CMD", "synthetic-command")
    monkeypatch.setenv("GH_HOST", "untrusted.example.invalid")

    def run(args, **kwargs):
        observed.append(kwargs)
        kwargs["stdout"].write(b"{}")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", run)
    candidate.command(["docker", "image", "inspect", "synthetic"])
    candidate.command(
        ["gh", "api", "--hostname", "github.com", "synthetic"], github=True
    )
    assert "GH_TOKEN" not in observed[0]["env"]
    assert observed[1]["env"]["GH_TOKEN"] == "synthetic-github-token"
    for call in observed:
        assert (
            not {"ANTHROPIC_API_KEY", "NIXPACKS_BUILD_CMD", "GH_HOST"}
            & call["env"].keys()
        )
        assert call["stderr"] == subprocess.DEVNULL and call["timeout"] == 60


@pytest.mark.parametrize(
    "value,code", [(b"x" * (candidate.MAX_METADATA + 1), 0), (b"{}", 1)]
)
def test_command_cap_and_failure_refused(monkeypatch, value, code):
    def run(args, **kwargs):
        kwargs["stdout"].write(value)
        return subprocess.CompletedProcess(args, code)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(candidate.CandidateRefused):
        candidate.command(["synthetic"])


@pytest.mark.parametrize(
    "mutation",
    ["revision", "platform", "public", "enabled", "duplicate", "missing", "nonstr"],
)
def test_provider_image_must_be_exact_revision_platform_and_dark_profile(mutation):
    config = image_config()
    if mutation == "revision":
        config["Config"]["Labels"]["org.opencontainers.image.revision"] = "0" * 40
    elif mutation == "platform":
        config["Architecture"] = "arm64"
    elif mutation == "public":
        config["Config"]["Env"][0] = "MOUNT_PROFILE=public"
    elif mutation == "enabled":
        config["Config"]["Env"][1] = "FEATURE_SESSION_PREP=true"
    elif mutation == "duplicate":
        config["Config"]["Env"].append("FEATURE_SESSION_PREP=false")
    elif mutation == "missing":
        config["Config"]["Env"].pop()
    else:
        config["Config"]["Env"].append(1)
    with pytest.raises(candidate.CandidateRefused):
        candidate.image_source(context(), config)


def test_exact_manifest_bytes_and_local_config_identity_required():
    raw = candidate.canonical(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"digest": CONFIG_DIGEST},
            "layers": [{"digest": IMAGE_DIGEST}],
        }
    )
    candidate.manifest_identity(raw, candidate.digest(raw), CONFIG_DIGEST)
    candidate.manifest_identity(raw + b"\n", candidate.digest(raw), CONFIG_DIGEST)
    with pytest.raises(candidate.CandidateRefused):
        candidate.manifest_identity(raw, IMAGE_DIGEST, CONFIG_DIGEST)
    with pytest.raises(candidate.CandidateRefused):
        candidate.manifest_identity(raw, candidate.digest(raw), IMAGE_DIGEST)


def test_source_archive_links_and_escape_refused(tmp_path):
    for name, kind in [("../escape", tarfile.REGTYPE), ("link", tarfile.SYMTYPE)]:
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w") as archive:
            item = tarfile.TarInfo(name)
            item.type = kind
            item.linkname = "../escape"
            archive.addfile(item, io.BytesIO(b""))
        with pytest.raises((candidate.CandidateRefused, tarfile.TarError)):
            candidate.extract_source(raw.getvalue(), tmp_path)


def test_write_never_overwrites_existing_or_symlink_output(tmp_path):
    output = tmp_path / "existing.json"
    candidate.write_new(output, record())
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert json.loads(output.read_bytes()) == record()
    with pytest.raises(OSError):
        candidate.write_new(output, record())
    link = tmp_path / "link.json"
    link.symlink_to(output)
    with pytest.raises(OSError):
        candidate.write_new(link, record())


def test_workflow_has_separate_prepare_and_provenance_only_selection():
    workflow = yaml.safe_load((ROOT / candidate.WORKFLOW).read_text())
    jobs = workflow["jobs"]
    assert {job["name"] for job in jobs.values()} == candidate.JOBS
    assert workflow[True] == {"workflow_dispatch": workflow[True]["workflow_dispatch"]}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] is False
    for name in ("prepare", "select"):
        job = jobs[name]
        assert job["needs"] == ["authorize", "test", "infrastructure"]
        for term in (
            "workflow_dispatch",
            "CosmaNeura/cosmaneura-sovereign",
            "refs/heads/main",
            "github.actor == 'JohnClem'",
            "github.triggering_actor == 'JohnClem'",
            f"inputs.mode == '{name}'",
        ):
            assert term in job["if"]
    assert (
        "vars.SOVEREIGN_PROVIDER_ARTIFACT_PREPARATION_ENABLED == 'true'"
        in jobs["prepare"]["if"]
    )
    assert jobs["select"]["permissions"] == {"contents": "read", "actions": "read"}
    selection_text = candidate.canonical(jobs["select"]).decode()
    assert "azure/login" not in selection_text and "az " not in selection_text
    assert (
        "id-token" not in selection_text
        and "deployment_authorized" not in selection_text
    )
    all_steps = candidate.canonical(jobs).decode()
    assert (
        "deploy-dual.sh" not in all_steps
        and "build-bootstrap-image.sh" not in all_steps
    )
    prepare_steps = jobs["prepare"]["steps"]
    names = [step.get("name", step.get("uses", "")) for step in prepare_steps]
    assert names.index(
        "Build isolated hash-locked provider image before Azure credentials"
    ) < next(
        index for index, name in enumerate(names) if name.startswith("azure/login@")
    )


def selection_arguments(output):
    return [
        "candidate",
        *"select --producer-run-id 10 --producer-attempt 1 --artifact-id 100 --output".split(),
        str(output),
    ]


def test_main_cli_refusal_is_value_free(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sys, "argv", selection_arguments(tmp_path / "selection.json"))
    monkeypatch.setattr(
        candidate,
        "admit",
        lambda *_args: (_ for _ in ()).throw(KeyError("synthetic-secret")),
    )
    assert candidate.main() == 1
    assert capsys.readouterr().err == "production_provider_candidate_refused\n"


@pytest.fixture
def dispatch_context(monkeypatch):
    for key, value in {
        "GITHUB_ACTOR": "JohnClem",
        "GITHUB_TRIGGERING_ACTOR": "JohnClem",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": candidate.REPOSITORY,
        "GITHUB_WORKFLOW_REF": candidate.WORKFLOW_REF,
        "GITHUB_SHA": SHA,
        "GITHUB_RUN_ID": "20",
        "GITHUB_RUN_ATTEMPT": "1",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        candidate,
        "source_identity",
        lambda sha: {"tree": TREE, "archive_sha256": IMAGE_DIGEST},
    )
    monkeypatch.setattr(candidate, "api", lambda path: {"object": {"sha": SHA}})


@pytest.mark.parametrize(
    "key,value",
    [
        ("GITHUB_ACTOR", "other"),
        ("GITHUB_TRIGGERING_ACTOR", "other"),
        ("GITHUB_EVENT_NAME", "push"),
        ("GITHUB_REF", "refs/heads/feature"),
        ("GITHUB_REPOSITORY", "other/repo"),
        ("GITHUB_WORKFLOW_REF", "other/workflow@main"),
        ("GITHUB_SHA", "a" * 39),
        ("GITHUB_RUN_ID", "0"),
        ("GITHUB_RUN_ATTEMPT", "01"),
    ],
)
def test_dispatch_source_context_has_founder_original_and_rerun_gates(
    monkeypatch, dispatch_context, key, value
):
    monkeypatch.setenv(key, value)
    with pytest.raises(candidate.CandidateRefused):
        candidate.source_context()


def test_source_context_rechecks_current_main_and_actual_source_contract(
    monkeypatch, dispatch_context
):
    accepted = candidate.source_context()
    assert accepted["build_contract_digest"] == candidate.digest(
        candidate.canonical(
            {
                path: candidate.digest((ROOT / path).read_bytes())
                for path in candidate.CONTRACT_FILES
            }
        )
    )
    monkeypatch.setattr(candidate, "api", lambda path: {"object": {"sha": "0" * 40}})
    with pytest.raises(candidate.CandidateRefused):
        candidate.source_context()


@pytest.mark.parametrize("mismatch", [False, True])
def test_stopped_image_source_proof_checks_bytes_and_always_cleans_owned_container(
    monkeypatch, tmp_path, mismatch
):
    calls = []
    root = tmp_path / "source"
    root.mkdir()
    (root / "fixture.py").write_bytes(b"synthetic source")
    monkeypatch.setattr(candidate, "ROOT", root)
    monkeypatch.setattr(candidate, "IMAGE_SOURCE_FILES", ("fixture.py",))

    def command(args, **kwargs):
        calls.append(args)
        if args[:2] == ["docker", "create"]:
            assert args[-1] == CONFIG_DIGEST and args[4:6] == ["--network", "none"]
            return b"a" * 64
        if args[:2] == ["docker", "cp"]:
            Path(args[-1]).write_bytes(
                b"incorrect" if mismatch else b"synthetic source"
            )
        return b""

    monkeypatch.setattr(candidate, "command", command)
    if mismatch:
        with pytest.raises(candidate.CandidateRefused):
            candidate.source_files_in_image(CONFIG_DIGEST)
    else:
        candidate.source_files_in_image(CONFIG_DIGEST)
    assert calls[-1][:2] == ["docker", "rm"]
    assert calls[-1][2] == calls[0][3]
    assert not any("start" in call or "exec" in call for call in calls)


def publisher(
    monkeypatch, *, stale=False, bad_profile=False, engine="classic", bad_export=False
):
    calls = []
    config, exported, raw = saved_fixture(RUNTIME, engine=engine)
    image_digest = candidate.digest(raw)
    if bad_export:
        selected = json.loads(exported["manifest.json"])[0]
        exported[selected["Config"]] = b"{}"
    if bad_profile:
        config["Config"]["Env"][0] = "MOUNT_PROFILE=public"

    def source_context():
        if stale and any(call[:2] == ["docker", "tag"] for call in calls):
            return {**context(), "source_tree": "0" * 40}
        return context()

    def command(args, **kwargs):
        calls.append(args)
        if args[:3] == ["docker", "image", "inspect"]:
            return candidate.canonical([config])
        if args[:3] == ["docker", "image", "save"]:
            assert args[-1] == config["Id"]
            save_fixture(Path(args[args.index("--output") + 1]), exported)
            return b""
        if args[:4] == ["docker", "buildx", "imagetools", "inspect"]:
            return (
                raw
                if "--raw" in args
                else candidate.canonical({"digest": image_digest})
            )
        if args[:4] == ["az", "acr", "repository", "show"]:
            return candidate.canonical(
                {
                    "digest": image_digest,
                    "changeableAttributes": {
                        "writeEnabled": False,
                        "deleteEnabled": False,
                        "readEnabled": True,
                        "listEnabled": True,
                    },
                }
            )
        return b""

    monkeypatch.setenv("PROVIDER_ARTIFACT_PREPARATION_ENABLED", "true")
    monkeypatch.setattr(candidate, "source_context", source_context)
    monkeypatch.setattr(candidate, "command", command)
    monkeypatch.setattr(
        candidate, "source_files_in_image", lambda value: calls.append(["proof", value])
    )
    return calls, image_digest


@pytest.mark.parametrize("engine", ["classic", "containerd"])
def test_publish_binds_source_bytes_and_config_id_then_locks_and_reads_both_references(
    monkeypatch, tmp_path, engine
):
    calls, image_digest = publisher(monkeypatch, engine=engine)
    output = tmp_path / "candidate.json"
    candidate.publish(output)
    result = json.loads(output.read_bytes())
    assert result["image"] == candidate.IMAGE + "@" + image_digest
    assert (
        result["deployment_authorized"] is False
        and result["dark_configuration"] == candidate.DARK_ENV
    )
    opaque_id = CONFIG_DIGEST if engine == "classic" else image_digest
    assert result["image_config_digest"] == CONFIG_DIGEST
    assert ["proof", opaque_id] in calls
    tag = next(call for call in calls if call[:2] == ["docker", "tag"])
    assert tag[2] == opaque_id
    assert next(
        i for i, args in enumerate(calls) if args[:3] == ["docker", "image", "save"]
    ) < next(i for i, args in enumerate(calls) if args[0] == "proof")
    imported = next(call for call in calls if call[:3] == ["az", "acr", "import"])
    assert (
        "--force" not in imported
        and imported[imported.index("--name") + 1] == candidate.REGISTRY
    )
    updates = [
        call for call in calls if call[:4] == ["az", "acr", "repository", "update"]
    ]
    reads = [call for call in calls if call[:4] == ["az", "acr", "repository", "show"]]
    assert len(updates) == len(reads) == 2
    assert all(
        call[call.index("--delete-enabled") + 1] == "false"
        and call[call.index("--write-enabled") + 1] == "false"
        for call in updates
    )
    assert not any("containerapp" in call or "deployment" in call for call in calls)


@pytest.mark.parametrize(
    "reason",
    [
        "disabled",
        "public",
        "stale_before_push",
        "proof_mismatch",
        "saved_config_mismatch",
    ],
)
def test_no_image_publication_or_descriptor_on_failed_precondition(
    monkeypatch, tmp_path, reason
):
    calls, _digest = publisher(
        monkeypatch,
        stale=reason == "stale_before_push",
        bad_profile=reason == "public",
        bad_export=reason == "saved_config_mismatch",
    )
    if reason == "disabled":
        monkeypatch.delenv("PROVIDER_ARTIFACT_PREPARATION_ENABLED")
    if reason == "proof_mismatch":
        monkeypatch.setattr(
            candidate, "source_files_in_image", lambda _value: candidate.require(False)
        )
    output = tmp_path / "candidate.json"
    with pytest.raises(candidate.CandidateRefused):
        candidate.publish(output)
    assert not output.exists()
    assert not any(
        call[:2] == ["docker", "push"] or call[:2] == ["az", "acr"] for call in calls
    )


def test_selection_receipt_references_authenticated_archive_without_granting_deployment(
    monkeypatch, tmp_path
):
    output = tmp_path / "selection.json"
    monkeypatch.setattr(sys, "argv", selection_arguments(output))
    monkeypatch.setattr(candidate, "admit", lambda *_args: (record(), IMAGE_DIGEST))
    monkeypatch.setenv("GITHUB_RUN_ID", "20")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    assert candidate.main() == 0
    result = json.loads(output.read_bytes())
    assert result["candidate"] == record() and result["artifact_id"] == 100
    assert result["artifact_archive_digest"] == IMAGE_DIGEST
    assert result["selection_run_id"] == 20 and result["selection_attempt"] == 1
    assert (
        result["purpose"] == "provider-artifact-provenance-selection-only"
        and result["deployment_authorized"] is False
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_builder_refuses_disabled_latch_or_wrong_vendor_bytes_before_execution(
    monkeypatch, enabled
):
    calls = []
    monkeypatch.setattr(candidate, "source_context", context)
    monkeypatch.setenv("PROVIDER_ARTIFACT_PREPARATION_ENABLED", str(enabled).lower())
    monkeypatch.setattr(candidate, "source_archive", lambda _context: b"synthetic")
    monkeypatch.setattr(candidate, "extract_source", lambda *_args: None)

    def command(args, **_kwargs):
        calls.append(args)
        return b"wrong synthetic vendor bytes"

    monkeypatch.setattr(candidate, "command", command)
    with pytest.raises(candidate.CandidateRefused):
        candidate.build()
    assert len(calls) == int(enabled)
    if enabled:
        assert calls[0][0] == "curl" and calls[0][-1] == candidate.NIXPACKS_URL


@pytest.mark.parametrize(
    "key,result",
    [
        ("accepted", None),
        ("config", (0, b"filter")),
        ("status", (0, b"dirty")),
        ("HEAD^{commit}", (0, b"0" * 40)),
        ("HEAD^{tree}", (0, b"invalid")),
    ],
)
def test_trusted_main_source_is_clean_exact_and_independent_of_parent_count(
    monkeypatch, key, result
):
    responses = {
        "config": (1, b""),
        "status": (0, b""),
        "HEAD^{commit}": (0, SHA.encode()),
        "HEAD^{tree}": (0, TREE.encode()),
    }
    if result is not None:
        responses[key] = result

    def git(repo, *args, **kwargs):
        index = args[-1] if args[0] == "rev-parse" else args[0]
        code, raw = responses[index]
        return subprocess.CompletedProcess(args, code, stdout=raw)

    monkeypatch.setattr(candidate, "_git", git)
    monkeypatch.setattr(candidate, "source_archive", lambda sha: sha.encode())
    if key == "accepted":
        assert candidate.source_identity(SHA) == {
            "tree": TREE,
            "archive_sha256": candidate.digest(SHA.encode()),
        }
    else:
        with pytest.raises(candidate.CandidateRefused):
            candidate.source_identity(SHA)
