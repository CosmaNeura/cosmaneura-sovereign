"""No-provider tests for the local combined staging source/recovery contract."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from time import time

import pytest

spec = importlib.util.spec_from_file_location(
    "local_staging_release",
    Path(__file__).resolve().parents[1] / "tools/local_staging_release.py",
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def selected():
    return {
        "sha": "a" * 40,
        "pr": 51,
        "reviewed_head": "b" * 40,
        "review_comment": 1,
        "review_sha256": "c" * 64,
    }


def plan():
    return {
        "version": 1,
        "core": selected(),
        "sovereign": selected(),
        "source_archive_sha256": "d" * 64,
        "recovery": {
            "image": m.IMAGE + "@sha256:" + "e" * 64,
            "revision": m.APP + "--old",
        },
        "public_before": {
            "image": m.IMAGE + "@sha256:" + "f" * 64,
            "revision": m.PUBLIC_APP + "--old",
        },
        "expires_at": int(time()) + 3600,
    }


def test_target_and_recovery_plan_are_staging_only_and_fresh():
    m.validate_plan(plan())
    for field, value in [
        ("image", "production.azurecr.io/sovereign@sha256:" + "e" * 64),
        ("revision", "another-app--old"),
    ]:
        bad = plan()
        bad["recovery"][field] = value
        with pytest.raises(ValueError):
            m.validate_plan(bad)
    bad = plan()
    bad["expires_at"] = int(time()) - 1
    with pytest.raises(ValueError):
        m.validate_plan(bad)


def reviews(reviewer="Cursor", prefix=None):
    chosen = selected()
    report = f"Independent {reviewer} review: no actionable findings.\n"
    metadata = {
        "reviewer": reviewer,
        "reviewed_head": chosen["reviewed_head"],
        "result": "accepted",
        "after_final_substantive_push": True,
        "report_sha256": hashlib.sha256(report.encode()).hexdigest(),
    }
    if prefix is None:
        prefix = m.PREFIX if reviewer == "Cursor" else "<!-- COSMANEURA_LOCAL_AGENT_REVIEW "
    body = prefix + json.dumps(metadata) + " -->\n" + report
    chosen["review_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    data = {
        "pull": {
            "merged": True,
            "base": {"ref": "main"},
            "merge_commit_sha": chosen["sha"],
            "head": {"sha": chosen["reviewed_head"]},
        },
        "ref": {"object": {"sha": chosen["sha"]}},
        "commit": {"tree": {"sha": "e" * 40}},
        "review": {
            "body": body,
            "user": {"login": "JohnClem", "type": "User"},
            "issue_url": "https://api.github.com/repos/CosmaNeura/cosmaneura-sovereign/issues/51",
        },
    }
    return chosen, data


def mock_review_source(monkeypatch, data):
    def gh(endpoint):
        return data[
            "pull"
            if "/pulls/" in endpoint
            else "ref"
            if "/git/ref/" in endpoint
            else "commit"
            if "/git/commits/" in endpoint
            else "review"
        ]

    monkeypatch.setattr(m, "gh", gh)


@pytest.mark.parametrize("reviewer", ["Cursor", "Codex"])
def test_current_merged_source_requires_exact_post_push_review(monkeypatch, reviewer):
    chosen, data = reviews(reviewer)
    mock_review_source(monkeypatch, data)
    m.verify_pair("cosmaneura-sovereign", chosen)
    for group, field, value in [
        ("pull", "merged", False),
        ("pull", "merge_commit_sha", "0" * 40),
        ("review", "body", "unreviewed"),
    ]:
        old = data[group][field]
        data[group][field] = value
        with pytest.raises(ValueError):
            m.verify_pair("cosmaneura-sovereign", chosen)
        data[group][field] = old
    data["ref"]["object"]["sha"] = "0" * 40
    with pytest.raises(ValueError):
        m.verify_pair("cosmaneura-sovereign", chosen)


@pytest.mark.parametrize(
    "reviewer,prefix",
    [
        ("Codex", m.PREFIX),
        ("Cursor", "<!-- COSMANEURA_LOCAL_AGENT_REVIEW "),
        ("Unrecognized", "<!-- COSMANEURA_LOCAL_AGENT_REVIEW "),
    ],
)
def test_review_envelope_must_identify_its_allowed_reviewer(monkeypatch, reviewer, prefix):
    chosen, data = reviews(reviewer, prefix)
    mock_review_source(monkeypatch, data)
    with pytest.raises(ValueError):
        m.verify_pair("cosmaneura-sovereign", chosen)


@pytest.mark.parametrize(
    "field,value",
    [
        ("reviewed_head", "0" * 40),
        ("result", "rejected"),
        ("after_final_substantive_push", False),
        ("after_final_substantive_push", 1),
        ("report_sha256", "0" * 64),
    ],
)
def test_codex_review_keeps_exact_acceptance_and_report_binding(monkeypatch, field, value):
    chosen, data = reviews("Codex")
    prefix = "<!-- COSMANEURA_LOCAL_AGENT_REVIEW "
    metadata, _, report = data["review"]["body"][len(prefix):].partition(" -->\n")
    metadata = json.loads(metadata)
    metadata[field] = value
    body = prefix + json.dumps(metadata) + " -->\n" + report
    data["review"]["body"] = body
    chosen["review_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    mock_review_source(monkeypatch, data)
    with pytest.raises(ValueError):
        m.verify_pair("cosmaneura-sovereign", chosen)


def test_source_rejects_unreviewed_merge_tree(monkeypatch):
    chosen, data = reviews()

    def gh(endpoint):
        if "/git/commits/" in endpoint:
            return {
                "tree": {
                    "sha": "0" * 40 if endpoint.endswith(chosen["sha"]) else "1" * 40
                }
            }
        return data["pull" if "/pulls/" in endpoint else "ref"]

    monkeypatch.setattr(m, "gh", gh)
    with pytest.raises(ValueError):
        m.verify_pair("cosmaneura-sovereign", chosen)


def test_state_requires_healthy_single_revision_serving_all_traffic(monkeypatch):
    properties = {
        "latestRevisionName": m.APP + "--old",
        "latestReadyRevisionName": m.APP + "--old",
        "template": {"containers": [{"image": "synthetic"}]},
        "configuration": {
            "activeRevisionsMode": "Single",
            "ingress": {"traffic": [{"latestRevision": True, "weight": 100}]},
        },
    }
    observed = {
        "healthState": "Healthy",
        "provisioningState": "Provisioned",
        "template": properties["template"],
    }
    monkeypatch.setattr(
        m,
        "az",
        lambda *args: {
            "properties": observed
            if args[:3] == ("containerapp", "revision", "show")
            else properties
        },
    )
    assert m.state(m.APP)["revision"] == m.APP + "--old"
    observed["healthState"] = "Unhealthy"
    with pytest.raises(ValueError):
        m.state(m.APP)
    observed["healthState"] = "Healthy"
    properties["configuration"]["ingress"]["traffic"][0]["weight"] = 50
    with pytest.raises(ValueError):
        m.state(m.APP)


def deployment_scenario(monkeypatch, recovery_failure=None):
    proposal = plan()
    current = copy.deepcopy(proposal["recovery"])
    calls = []
    published = {
        "complete": True,
        "plan_sha256": "1" * 64,
        "source_sha": "a" * 40,
        "core_sha": "a" * 40,
        "image": m.IMAGE + "@sha256:" + "2" * 64,
    }
    monkeypatch.setattr(
        m,
        "state",
        lambda name: (
            current if name == m.APP else proposal["public_before"]
        ),
    )

    def az(*args):
        calls.append(args)
        if args[:3] == ("acr", "manifest", "show-metadata"):
            return {"digest": args[-1].split("@")[1]}
        if args[:2] == ("containerapp", "update"):
            suffix = args[args.index("--revision-suffix") + 1]
            current.update(
                {
                    "image": published["image"],
                    "revision": m.APP + "--" + suffix,
                }
            )
            return {}
        if args[:3] == ("containerapp", "revision", "show"):
            assert args[-1] == proposal["recovery"]["revision"]
            if recovery_failure == "missing":
                raise m.subprocess.CalledProcessError(1, "az")
            previous = {
                "provisioningState": "Provisioned",
                "template": {"containers": [{"image": proposal["recovery"]["image"]}]},
            }
            if recovery_failure == "wrong_image":
                previous["template"]["containers"][0]["image"] = published["image"]
            elif recovery_failure == "failed":
                previous["provisioningState"] = "Failed"
            elif recovery_failure == "empty":
                previous["template"]["containers"] = []
            elif recovery_failure == "extra_container":
                previous["template"]["containers"].append({"image": "unexpected"})
            return {"properties": previous}
        return {
            "properties": {
                "latestReadyRevisionName": current["revision"],
                "configuration": (
                    {}
                    if recovery_failure == "missing_retention"
                    else {"maxInactiveRevisions": 0 if recovery_failure == "zero" else 10}
                ),
            }
        }

    monkeypatch.setattr(m, "az", az)
    return proposal, published, calls


def test_deployment_preserves_public_mount_and_rejects_drift_before_update(
    monkeypatch, capsys
):
    proposal, published, calls = deployment_scenario(monkeypatch)
    original = copy.deepcopy(proposal)
    m.deploy(proposal, "1" * 64, published)
    updates = [call for call in calls if call[:2] == ("containerapp", "update")]
    assert len(updates) == 1 and updates[0][updates[0].index("-n") + 1] == m.APP
    assert proposal == original
    assert "FEATURE_ATHENA_LABS=false" in updates[0]
    assert "SESSION_PREP_CONTEXT_SOURCE=platform" in updates[0]
    assert updates[0][updates[0].index("--max-inactive-revisions") + 1] == "10"
    report = json.loads(capsys.readouterr().out)
    assert report["recovery"] == original["recovery"]
    assert report["recovery_revision_retained"] is True
    calls.clear()
    monkeypatch.setattr(
        m, "state", lambda name: {"image": "drift", "revision": "drift"}
    )
    with pytest.raises(ValueError):
        m.deploy(proposal, "1" * 64, published)
    assert not calls


@pytest.mark.parametrize(
    "failure",
    [
        "zero", "missing_retention", "missing", "wrong_image", "failed", "empty",
        "extra_container",
    ],
)
def test_deployment_does_not_report_success_without_exact_recovery(
    monkeypatch, capsys, failure
):
    proposal, published, calls = deployment_scenario(monkeypatch, failure)
    with pytest.raises((ValueError, m.subprocess.CalledProcessError)):
        m.deploy(proposal, "1" * 64, published)
    assert capsys.readouterr().out == ""
    updates = [call for call in calls if call[:2] == ("containerapp", "update")]
    assert len(updates) == 1
    assert updates[0][updates[0].index("-n") + 1] == m.APP


def azure_container_pair():
    revision = {
        "name": "provider",
        "image": "registry/image@sha256:" + "a" * 64,
        "command": ["python"],
        "args": ["server.py"],
        "env": [
            {"name": "KEY", "secretRef": "key-ref"},
            {"name": "MOUNT_PROFILE", "value": "provider_assistant"},
        ],
        "resources": {"cpu": 0.5, "memory": "1Gi"},
        "probes": [
            {
                "type": "Readiness",
                "periodSeconds": 10,
                "httpGet": {"path": "/healthz", "port": 8000, "scheme": "HTTP"},
            }
        ],
    }
    app = copy.deepcopy(revision)
    app["imageType"] = "ContainerImage"
    app["env"][0]["value"] = ""
    app["resources"]["ephemeralStorage"] = "2Gi"
    app["probes"][0]["initialDelaySeconds"] = 0
    app["probes"][0]["httpGet"].update(host="", httpHeaders=[])
    return app, revision


def serve_pair(monkeypatch, app, revision):
    properties = {
        "latestRevisionName": m.APP + "--old",
        "latestReadyRevisionName": m.APP + "--old",
        "template": {"containers": [app]},
        "configuration": {
            "activeRevisionsMode": "Single",
            "ingress": {"traffic": [{"latestRevision": True, "weight": 100}]},
        },
    }
    observed = {
        "healthState": "Healthy",
        "provisioningState": "Provisioned",
        "template": {"containers": [revision]},
    }
    monkeypatch.setattr(
        m,
        "az",
        lambda *args: {
            "properties": observed
            if args[:3] == ("containerapp", "revision", "show")
            else properties
        },
    )


@pytest.mark.parametrize("empty", [None, ""])
def test_state_accepts_observed_azure_defaults_without_mutating_response(
    monkeypatch, empty
):
    app, revision = azure_container_pair()
    app["env"][0]["value"] = empty
    original = copy.deepcopy(app)
    serve_pair(monkeypatch, app, revision)
    assert m.state(m.APP) == {"image": app["image"], "revision": m.APP + "--old"}
    assert app == original


@pytest.mark.parametrize(
    "path,value",
    [
        (("image",), "different-image"),
        (("imageType",), "Artifact"),
        (("command",), ["other"]),
        (("args",), ["other.py"]),
        (("env", 0, "secretRef"), "different-ref"),
        (("env", 0, "value"), "unexpected-inline-value"),
        (("env", 1, "value"), "public"),
        (("resources", "cpu"), 1),
        (("resources", "memory"), "2Gi"),
        (("resources", "ephemeralStorage"), "4Gi"),
        (("probes", 0, "initialDelaySeconds"), 5),
        (("probes", 0, "periodSeconds"), 30),
        (("probes", 0, "httpGet", "host"), "other.internal"),
        (("probes", 0, "httpGet", "httpHeaders"), [{"name": "X-Test", "value": "1"}]),
        (("probes", 0, "httpGet", "path"), "/other"),
        (("probes", 0, "httpGet", "port"), 9000),
        (("probes", 0, "httpGet", "scheme"), "HTTPS"),
        (("unknownField",), "unexpected"),
    ],
)
def test_state_still_refuses_substantive_container_drift(monkeypatch, path, value):
    app, revision = azure_container_pair()
    target = app
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    serve_pair(monkeypatch, app, revision)
    with pytest.raises(ValueError, match="local_staging_release_refused"):
        m.state(m.APP)
