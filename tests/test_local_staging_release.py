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


def reviews():
    chosen = selected()
    report = "Independent Cursor review: no actionable findings.\n"
    metadata = {
        "reviewer": "Cursor",
        "reviewed_head": chosen["reviewed_head"],
        "result": "accepted",
        "after_final_substantive_push": True,
        "report_sha256": hashlib.sha256(report.encode()).hexdigest(),
    }
    body = m.PREFIX + json.dumps(metadata) + " -->\n" + report
    chosen["review_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    data = {
        "pull": {
            "merged": True,
            "base": {"ref": "main"},
            "merge_commit_sha": chosen["sha"],
            "head": {"sha": chosen["reviewed_head"]},
        },
        "ref": {"object": {"sha": chosen["sha"]}},
        "review": {
            "body": body,
            "user": {"login": "JohnClem", "type": "User"},
            "issue_url": "https://api.github.com/repos/CosmaNeura/cosmaneura-sovereign/issues/51",
        },
    }
    return chosen, data


def test_current_merged_source_requires_exact_post_push_cursor_review(monkeypatch):
    chosen, data = reviews()

    def gh(endpoint):
        return data[
            "pull"
            if "/pulls/" in endpoint
            else "ref"
            if "/git/ref/" in endpoint
            else "review"
        ]

    monkeypatch.setattr(m, "gh", gh)
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


def test_deployment_preserves_public_mount_and_rejects_drift_before_update(monkeypatch):
    proposal = plan()
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
            proposal["recovery"] if name == m.APP else proposal["public_before"]
        ),
    )

    def az(*args):
        calls.append(args)
        if args[:3] == ("acr", "manifest", "show-metadata"):
            return {"digest": args[-1].split("@")[1]}
        if args[:2] == ("containerapp", "update"):
            suffix = args[args.index("--revision-suffix") + 1]
            proposal["recovery"] = {
                "image": published["image"],
                "revision": m.APP + "--" + suffix,
            }
            return {}
        return {
            "properties": {"latestReadyRevisionName": proposal["recovery"]["revision"]}
        }

    monkeypatch.setattr(m, "az", az)
    original = copy.deepcopy(proposal["public_before"])
    m.deploy(proposal, "1" * 64, published)
    updates = [call for call in calls if call[:2] == ("containerapp", "update")]
    assert len(updates) == 1 and updates[0][updates[0].index("-n") + 1] == m.APP
    assert proposal["public_before"] == original
    assert "FEATURE_ATHENA_LABS=false" in updates[0]
    assert "SESSION_PREP_CONTEXT_SOURCE=platform" in updates[0]
    calls.clear()
    monkeypatch.setattr(
        m, "state", lambda name: {"image": "drift", "revision": "drift"}
    )
    with pytest.raises(ValueError):
        m.deploy(proposal, "1" * 64, published)
    assert not calls
