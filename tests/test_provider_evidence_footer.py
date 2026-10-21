"""Known terminal boilerplate is disclosed without removing quoted evidence."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest

from app.clients.provider_evidence import validated_evidence
from tests.test_provider_evidence import (
    RenderedEvidence, output, panel_render, selected, source_context_with_sections,
)


CARE = (
    "Greater than {minutes} minutes was spent in patient care, including review "
    "of prior records, consultation with other providers, and documentation."
)
AI = "This note was generated with AI assistance."
CLINICAL = (
    "The prior proposed losartan 50 mg dose is superseded.\n"
    "Continue losartan 25 mg daily. CBC order cancelled; no result documented."
)


def footer(minutes=0, separator="\n\n", trailing=""):
    return separator + CARE.format(minutes=minutes) + separator + AI + trailing


def evidence(text):
    context = source_context_with_sections({"P - PLAN": text})
    result = validated_evidence(output(selected()), context, agent="synthetic", data={})
    assert result.data["record_evidence_status"] == "validated_excerpts"
    return result


class Disclosure(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.details = []
        self.summaries = []
        self.active_summary = False
        self.stack = []
        self.summary_parents = []

    def handle_starttag(self, tag, attrs):
        if tag == "details":
            self.details.append(dict(attrs))
        if tag == "summary":
            self.summary_parents.append(self.stack[-1])
            self.summaries.append("")
            self.active_summary = True
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag == "summary":
            self.active_summary = False
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()

    def handle_data(self, text):
        if self.active_summary:
            self.summaries[-1] += text


@pytest.mark.parametrize("hydrated", [False, True])
@pytest.mark.parametrize("minutes", [0, 1])
@pytest.mark.parametrize("separator,trailing", [("\n\n", ""), ("\n\n\n", "\n\n")])
def test_known_terminal_footer_preserves_full_quote_date_and_native_disclosure(
    hydrated, minutes, separator, trailing,
):
    suffix = footer(minutes, separator, trailing)
    text = CLINICAL + suffix
    result = evidence(text)
    html = panel_render(result.content, hydrated=hydrated, structured=result.data)
    rendered = RenderedEvidence()
    rendered.feed(html)
    assert rendered.quotes == [CLINICAL, suffix]
    assert "".join(rendered.quotes) == text
    assert rendered.labels == ["2026-09-30 — P - PLAN"]
    assert result.citations[0].title == rendered.labels[0]
    parsed = Disclosure()
    parsed.feed(html)
    assert parsed.details == [{"class": "cnp-evidence-additional"}]
    assert parsed.summaries == ["Additional note text"]
    assert parsed.summary_parents == ["details"]
    # Native summary remains keyboard-operable; no custom role/tabindex or
    # default-open override replaces the browser's disclosure semantics.
    assert '<details class="cnp-evidence-additional"><summary>' in html
    assert html.index("CBC order cancelled") < html.index("<details")


@pytest.mark.parametrize("hydrated", [False, True])
@pytest.mark.parametrize("text", [
    CLINICAL + footer(2),
    CLINICAL + footer().replace("patient care", "patient assessment"),
    CLINICAL + footer().replace("AI assistance.", "AI assistance; verify the CBC status."),
    CLINICAL + footer().replace("\n\n" + AI, "\n" + AI),
    CLINICAL + footer() + "\nCBC was subsequently resulted.",
    CLINICAL + footer().replace("\n\n" + AI, "\n\nDo not cancel the CBC.\n\n" + AI),
    CLINICAL + "\n\n" + AI,
    CLINICAL + "\n\n" + CARE.format(minutes=0),
    CLINICAL + footer().replace("Greater than", " Greater than"),
    CLINICAL + footer() + " ",
    footer(),
    "\n\n" + footer(),
    CLINICAL + '\n\n"' + CARE.format(minutes=0) + "\n\n" + AI + '"',
])
def test_unknown_nonterminal_or_clinical_neighbor_text_remains_fully_visible(hydrated, text):
    result = evidence(text)
    html = panel_render(result.content, hydrated=hydrated, structured=result.data)
    rendered = RenderedEvidence()
    rendered.feed(html)
    assert rendered.quotes == [text]
    assert "<details" not in html and "<summary" not in html


@pytest.mark.parametrize("hydrated", [False, True])
def test_clinical_prefix_keeps_markup_literal_when_footer_is_disclosed(hydrated):
    clinical = '<script>synthetic()</script> <img src=x onerror=synthetic()>\n' + CLINICAL
    text = clinical + footer()
    result = evidence(text)
    html = panel_render(result.content, hydrated=hydrated, structured=result.data)
    rendered = RenderedEvidence()
    rendered.feed(html)
    assert rendered.quotes == [clinical, footer()]
    assert not {"script", "img", "a"}.intersection(rendered.tags)
    assert "&lt;script&gt;" in html and "&lt;img " in html
    assert "".join(rendered.quotes) == text


def test_disclosure_live_answer_and_history_replay_render_identically():
    result = evidence(CLINICAL + footer(1))
    assert panel_render(result.content, hydrated=False, structured=result.data) == panel_render(
        result.content, hydrated=True,
    )
    source = (Path(__file__).parents[1] / "app/panel.js").read_text()
    assert ".cnp-evidence-additional summary:focus-visible{outline:2px solid var(--accent)" in source
