"""Knowledge base units: extraction, chunking, quotation limits and reference claims (no DB)."""

import sys
from pathlib import Path

from healthops.assistant.cards import build_cards, quotable
from healthops.assistant.claims import Claim, FindingsParams, render_explanation, verify_claim
from healthops.knowledge.ingest import SHORT_QUOTE_CHARS, SourceSpec, own_document_specs
from healthops.knowledge.text import (
    MAX_CHARS,
    Passage,
    chunk_passages,
    passages_from_apple_json,
    passages_from_html,
    passages_from_markdown,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/knowledge"))

from fetch_sources import KNOWLEDGE_DIR  # noqa: E402


def test_html_extraction_keeps_headings_and_drops_navigation():
    raw = """<html><head><script>x()</script><style>p{}</style></head><body>
    <nav><a>Home</a></nav><h2>Heart rate</h2><p>The average resting heart rate is about 75 bpm.</p>
    <p>It can range from 60 to 100 in some individuals.</p><footer>Legal</footer></body></html>"""
    passages = passages_from_html(raw)
    assert [p.section for p in passages] == ["Heart rate"]
    assert (
        "75 bpm" in passages[0].text
        and "Home" not in passages[0].text
        and "Legal" not in passages[0].text
    )


def test_markdown_and_apple_json_extraction():
    md = "# Title\n\nIntro line.\n\n## Local day\nA local day is midnight to midnight.\n"
    sections = {p.section: p.text for p in passages_from_markdown(md)}
    assert sections["Title"] == "Intro line." and "midnight" in sections["Local day"]
    apple = (
        '{"abstract": [{"type": "text", "text": "A quantity sample type."}], '
        '"metadata": {"title": "SDNN"}, "primaryContentSections": []}'
    )
    assert passages_from_apple_json(apple)[0].text == "A quantity sample type."


def test_chunking_merges_short_passages_and_splits_long_ones():
    a = "This is the first short paragraph of the section, long enough to keep."
    b = "This is the second short paragraph, which should merge with the first one."
    other = "A different section starts a new chunk even when it is short enough to merge."
    merged = chunk_passages(
        [Passage("s", None, a), Passage("s", None, b), Passage("t", None, other)]
    )
    assert [p.section for p in merged] == ["s", "t"]
    assert merged[0].text == a + "\n" + b
    assert chunk_passages([Passage("s", None, "tiny")]) == []  # fragments under 40 chars drop
    long_text = " ".join(f"Sentence number {i} is here." for i in range(200))
    pieces = chunk_passages([Passage("long", 3, long_text)])
    assert len(pieces) > 1 and all(len(p.text) <= MAX_CHARS for p in pieces)
    assert all(p.page == 3 and p.section == "long" for p in pieces)
    assert all(p.text.endswith(".") for p in pieces)  # split at sentence boundaries


def test_markdown_provenance_lines_are_not_quoted():
    md = "# Glossary\n\nSource: this project.\nLicence: project.\n\n- warning_a: meaning a.\n"
    assert passages_from_markdown(md)[0].text == "- warning_a: meaning a."


def test_quotable_respects_the_limit_at_a_sentence_boundary():
    text = (
        "First sentence is here. Second sentence follows. Third one is long enough to be cut off."
    )
    assert quotable(text, 1000) == text
    cut = quotable(text, 50)
    assert cut == "First sentence is here. Second sentence follows." and len(cut) <= 50
    assert quotable("No punctuation at all " * 20, 40).endswith("...")


def test_quote_limit_follows_the_allowed_use():
    short = SourceSpec(
        "a",
        "t",
        "p",
        "u",
        "Copyright",
        "short verbatim quotation with link only",
        "html",
        Path("x"),
        [],
    )
    full = SourceSpec(
        "b", "t", "p", "u", "Public domain", "verbatim quotation with link", "html", Path("x"), []
    )
    assert (
        short.quote_limit_chars == SHORT_QUOTE_CHARS and full.quote_limit_chars > SHORT_QUOTE_CHARS
    )
    own = own_document_specs(KNOWLEDGE_DIR / "own", "https://example.test")
    assert {s.id for s in own} >= {"own-statistical-definitions", "own-warnings-glossary"}
    assert all(s.kind == "markdown" for s in own)


REFERENCE = {
    "tool": "lookup_reference",
    "arguments": {"query": "sleep"},
    "result": {
        "query": "sleep",
        "references": [
            {
                "source_id": "nhlbi-how-much-sleep",
                "source_title": "How Much Sleep Is Enough?",
                "publisher": "NHLBI",
                "url": "https://example.test/sleep",
                "license": "Public domain",
                "section": "Adults",
                "page": None,
                "quote_limit_chars": 300,
                "text": "Experts recommend that adults sleep between 7 and 9 hours a night. " * 8,
                "similarity": 0.7,
            }
        ],
        "warnings": [],
        "definitions": {},
    },
}


def test_reference_cards_and_claims_quote_only_the_allowed_span():
    cards = build_cards([REFERENCE])
    assert len(cards) == 1 and cards[0].kind == "reference" and cards[0].evidence_id == "e1.r1"
    assert len(cards[0].text) <= 300 and cards[0].text.endswith(".")
    ok = verify_claim(Claim(type="reference", evidence_id="e1.r1"), cards)
    assert ok.valid and ok.sentence.startswith('How Much Sleep Is Enough? (NHLBI, Adults) says: "')
    assert "not an assessment of your own values" in ok.sentence
    # Wrong claim types against a reference card, and reference claims against data cards, fail.
    assert not verify_claim(
        Claim(type="value_on_date", evidence_id="e1.r1", date="2026-10-01"), cards
    ).valid
    assert not verify_claim(Claim(type="missing_data", evidence_id="e1.r1"), cards).valid
    text, claims, all_valid = render_explanation(
        FindingsParams(findings=["reference e1.r1"]), cards
    )
    assert all_valid and text == ok.sentence


def test_reference_claim_needs_a_reference_card():
    from test_assistant import SUMMARY

    cards = build_cards([{"tool": "daily_summary", "arguments": {}, "result": SUMMARY}])
    bad = verify_claim(Claim(type="reference", evidence_id="e1.healthkit"), cards)
    assert not bad.valid and "reference card" in bad.reason
