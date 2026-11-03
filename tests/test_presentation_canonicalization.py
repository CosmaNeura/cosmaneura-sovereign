"""Set-canonical provider presentation stays compatible with Core history."""

from __future__ import annotations

import itertools
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from app.schemas import (
    ALL_PRESENTATION_SUPPRESSIONS,
    EmergencyPresentation,
    SensitiveDisclosurePresentation,
    StandardPresentation,
)


@pytest.mark.parametrize(
    "model,mode",
    [(StandardPresentation, "standard"), (EmergencyPresentation, "emergency"),
     (SensitiveDisclosurePresentation, "sensitive_disclosure")],
)
def test_all_valid_suppression_sets_serialize_canonically(model, mode):
    names = ALL_PRESENTATION_SUPPRESSIONS
    subsets = [names] if mode == "sensitive_disclosure" else itertools.chain.from_iterable(
        itertools.combinations(names, size) for size in range(len(names) + 1)
    )
    contact = {"label": "Contact support", "action_id": "contact-support",
               "fallback_text": "Contact your support person."}
    for subset in subsets:
        supplied = list(reversed(subset))
        wire = model(mode=mode, suppress=supplied, human_contact=contact).model_dump(mode="json")
        assert list(wire) == ["mode", "suppress", "human_contact"]
        assert wire["suppress"] == sorted(subset)
        assert supplied == list(reversed(subset))
        assert list(wire["human_contact"]) == ["label", "action_id", "fallback_text"]
        assert wire["human_contact"] == contact


def test_factual_provider_wire_matches_core_canonical_history_in_actual_panel():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    root = Path(__file__).parents[1]
    wire = StandardPresentation(
        mode="standard", suppress=["intent_badge", "doctrinal_badge"], human_contact=None,
    ).model_dump(mode="json")
    payload = {"hydrated": False, "history_presentation": {
        "mode": "standard", "suppress": ["doctrinal_badge", "intent_badge"], "human_contact": None,
    }, "result": {
        "client_turn_id": "00000000-0000-4000-8000-000000000001",
        "answer": "Fictional factual answer remains available.", "intent": "engagement",
        "presentation": wire, "citations": [], "doctrinal_review": None,
        "structured": {}, "safety_notice": None,
    }}
    result = subprocess.run(
        [node, str(root / "tests/panel_evidence_harness.js"), str(root / "app/panel.js")],
        input=json.dumps(payload), text=True, capture_output=True, check=True, timeout=30,
    )
    assert payload["result"]["answer"] in json.loads(result.stdout)["html"]
