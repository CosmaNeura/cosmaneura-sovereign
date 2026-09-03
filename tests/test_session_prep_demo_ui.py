"""Accessibility and controller checks for the loopback Session Prep demo."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
DEMO_PATH = ROOT / "tools" / "session_prep_demo.html"
HARNESS_PATH = ROOT / "tests" / "session_prep_demo_ui_harness.js"


class _Node:
    def __init__(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tag = tag
        self.attrs = {name: value or "" for name, value in attrs}
        self.text = ""


class _Document(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.nodes: list[_Node] = []
        self.stack: list[_Node] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, attrs)
        self.nodes.append(node)
        if tag not in {"meta", "link", "img", "input", "br", "hr", "source"}:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        for node in self.stack:
            node.text += data


def _document() -> _Document:
    document = _Document()
    document.feed(DEMO_PATH.read_text(encoding="utf-8"))
    return document


def _color(value: str) -> tuple[float, float, float]:
    value = value.removeprefix("#")
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    channels = tuple(int(value[index : index + 2], 16) / 255 for index in (0, 2, 4))
    return tuple(
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    )


def _luminance(value: str) -> float:
    red, green, blue = _color(value)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(foreground: str, background: str) -> float:
    first, second = _luminance(foreground), _luminance(background)
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


def _variable(css: str, name: str) -> str:
    match = re.search(rf"--{re.escape(name)}:(#[0-9a-fA-F]{{3,8}})", css)
    assert match is not None, f"missing CSS variable {name}"
    return match.group(1)


def test_demo_has_a_named_four_state_control_and_live_feedback() -> None:
    document = _document()

    html = next(node for node in document.nodes if node.tag == "html")
    assert html.attrs["lang"] == "en"
    assert [node.text.strip() for node in document.nodes if node.tag == "title"] == [
        "Session Prep synthetic vertical slice"
    ]
    assert len([node for node in document.nodes if node.tag == "h1"]) == 1

    headings = {
        node.attrs.get("id"): node.text.strip()
        for node in document.nodes
        if node.tag == "h2"
    }
    assert headings["state-controls-title"] == "Exercise each typed state"
    assert headings["trace-title"] == "Sanitized Core wire trace"

    group = next(node for node in document.nodes if node.attrs.get("class") == "modes")
    assert group.attrs == {
        "class": "modes",
        "role": "group",
        "aria-labelledby": "state-controls-title",
    }

    buttons = [
        node
        for node in document.nodes
        if node.tag == "button" and "mode" in node.attrs.get("class", "").split()
    ]
    assert [button.attrs["data-mode"] for button in buttons] == [
        "ready",
        "refreshing",
        "pending",
        "unavailable",
    ]
    assert [button.text.strip() for button in buttons] == [
        "Ready",
        "Refreshing",
        "Pending",
        "Unavailable",
    ]
    assert all(button.attrs["type"] == "button" for button in buttons)
    assert all(button.attrs["aria-pressed"] == "false" for button in buttons)

    by_id = {node.attrs["id"]: node for node in document.nodes if "id" in node.attrs}
    status = by_id["demo-status"]
    assert status.attrs["role"] == "status"
    assert status.attrs["aria-live"] == "polite"
    assert status.attrs["aria-atomic"] == "true"
    assert status.attrs["aria-busy"] == "true"

    trace_status = by_id["trace-status"]
    assert trace_status.attrs["role"] == "status"
    assert trace_status.attrs["aria-live"] == "polite"
    assert trace_status.attrs["aria-atomic"] == "true"
    calls = by_id["calls"]
    assert calls.attrs["role"] == "list"
    assert calls.attrs["aria-label"] == "Sanitized Core calls"


def test_demo_styles_meet_the_declared_text_contrast_floor() -> None:
    document = _document()
    css = "\n".join(node.text for node in document.nodes if node.tag == "style")
    foregrounds = {
        "ink": _variable(css, "ink"),
        "muted": _variable(css, "muted"),
        "eyebrow": _variable(css, "eyebrow"),
    }
    gradient_backgrounds = ("#f7fafc", "#e8f0f5", "#e7edf3")

    for label, foreground in foregrounds.items():
        for background in gradient_backgrounds:
            assert _contrast(foreground, background) >= 4.5, (
                f"{label} text contrast is below 4.5:1 on {background}"
            )

    for foreground, background in (
        ("#b9c9dc", _variable(css, "navy")),
        (_variable(css, "ok"), "#e2f4e9"),
        ("#ffffff", _variable(css, "navy")),
        ("#ffffff", _variable(css, "blue")),
        ("#ffffff", "#795f20"),
        ("#ffffff", "#59687b"),
        ("#ffffff", "#7a4250"),
        (_variable(css, "blue"), "#e7eef5"),
        ("#6a5425", "#fff8e9"),
        ("#4e6279", "#ffffff"),
    ):
        assert _contrast(foreground, background) >= 4.5

    assert "button:focus-visible" in css
    assert "outline:3px solid var(--blue)" in css
    assert "@media(max-width:850px)" in css
    assert "@media(prefers-reduced-motion:reduce)" in css


def test_demo_assets_and_controller_are_local_and_non_persistent() -> None:
    document = _document()
    source = DEMO_PATH.read_text(encoding="utf-8")
    resource_nodes = [
        node
        for node in document.nodes
        if node.tag in {"script", "link", "img", "iframe", "audio", "video", "source"}
    ]
    resource_urls = [
        node.attrs[name]
        for node in resource_nodes
        for name in ("src", "href")
        if node.attrs.get(name)
    ]
    assert resource_urls == ["/embed/v1/panel.js"]
    assert "https://" not in source
    assert "http://" not in source
    assert 'fetch("/demo/session-prep/state/"+mode,{method:"POST"})' in source
    assert 'fetch("/demo/session-prep/trace",{cache:"no-store"})' in source
    assert "localStorage" not in source
    assert "sessionStorage" not in source
    assert not [
        node for node in document.nodes if node.tag in {"input", "textarea", "form"}
    ]


def test_demo_controller_runtime_accessibility_and_failure_behavior() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    subprocess.run(
        [node, str(HARNESS_PATH), str(DEMO_PATH)],
        check=True,
        capture_output=True,
        text=True,
    )
