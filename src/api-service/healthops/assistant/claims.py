"""Structured findings: the model picks evidence references, Python reads the values.

Evidence references support daily values, windows, comparisons, inventory and quotations.
Every claim is checked against the cards built from the
evidence; a single invalid claim degrades the whole explanation to a fixed fallback so that no
partial, possibly misleading, sentence is shown. Direction of change is computed here, never
taken from the model.
"""

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from healthops.assistant.cards import WARNING_TEXT, Card, format_value
from healthops.assistant.reference_safety import reference_allowed

FALLBACK_EXPLANATION = (
    "An explanation could not be generated reliably for this answer. The data cards below show "
    "exactly what was found."
)

CLAIM_TYPES = (
    "value_on_date",
    "window_difference",
    "missing_data",
    "reference",
    "window_summary",
    "available_series",
)
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal[
        "value_on_date",
        "window_difference",
        "missing_data",
        "reference",
        "window_summary",
        "available_series",
    ]
    evidence_id: str = Field(description="Card id to cite, e.g. e1.healthkit")
    date: str | None = Field(default=None, description="ISO date for value_on_date or missing_data")


class FindingsParams(BaseModel):
    """Flat text grammar instead of nested objects: small local models echo JSON-schema
    fragments into nested parameters, so each finding is one string
    ``"<type> <evidence_id> [<date>]"``. Parsing is tolerant; verification is not."""

    model_config = ConfigDict(extra="ignore")
    findings: list[str] = Field(
        min_length=1,
        max_length=31,  # a month of per-day findings degrades the explanation, not the protocol
        description="Each item: '<type> <evidence_id> [<ISO date>]' with type one of "
        "value_on_date, window_difference, missing_data, window_summary, available_series, "
        "reference. Example: "
        "'value_on_date e1.healthkit 2026-10-01'.",
    )

    @property
    def claims(self) -> list["Claim | str"]:
        """Parsed claims; unparseable strings stay as text and fail verification."""
        parsed: list[Claim | str] = []
        seen: set[tuple] = set()
        for text in self.findings:
            parts = str(text).replace(",", " ").split()
            if len(parts) >= 2 and parts[0] in CLAIM_TYPES:
                # Extra tokens (metric names, second dates) are ignored; the first ISO date counts.
                dates = [t for t in parts[2:] if _ISO_DATE.fullmatch(t)]
                claim = Claim(type=parts[0], evidence_id=parts[1], date=dates[0] if dates else None)
                key = (claim.type, claim.evidence_id, claim.date)
                if key in seen:
                    continue  # identical repeated findings are rendered once
                seen.add(key)
                parsed.append(claim)
            else:
                parsed.append(str(text))
        return parsed


class VerifiedClaim(BaseModel):
    claim: Claim | None  # None when the finding text could not be parsed
    raw: str | None = None
    valid: bool
    reason: str | None = None
    sentence: str | None = None


def _card(cards: list[Card], evidence_id: str) -> Card | None:
    return next((c for c in cards if c.evidence_id == evidence_id), None)


def _calendar_date(text: str | None) -> date | None:
    """A real calendar date, or None. '2026-09-31' is text that matches the pattern but no day."""
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _inside(card: Card, day: date) -> bool:
    start, end = _calendar_date(card.start_date), _calendar_date(card.end_date)
    return bool(start and end and start <= day <= end)


def _row_sentence_unusable(card: Card, row) -> str:
    detail = "; ".join(WARNING_TEXT.get(code, code.replace("_", " ")) for code in row.warnings)
    provider = f" ({card.provider})" if card.provider else ""
    return (
        f"On {row.date}, {card.metric} records exist{provider} but no reliable value could be "
        f"derived{': ' + detail if detail else ''}."
    )


def _window_text(start: str | None, end: str | None) -> str:
    return start if start == end else f"{start} to {end}"


def verify_claim(claim: Claim, cards: list[Card]) -> VerifiedClaim:
    card = _card(cards, claim.evidence_id)
    if card is None:
        return VerifiedClaim(claim=claim, valid=False, reason="unknown evidence id")
    if claim.date is not None and _calendar_date(claim.date) is None:
        return VerifiedClaim(claim=claim, valid=False, reason="not a calendar date")
    if claim.type == "reference" or card.kind == "reference":
        if claim.type != "reference" or card.kind != "reference":
            return VerifiedClaim(
                claim=claim, valid=False, reason="reference claims need a reference card"
            )
        if not card.text:
            return VerifiedClaim(claim=claim, valid=False, reason="reference card has no text")
        if not reference_allowed(
            {
                "text": card.text,
                "source_title": card.title,
                "publisher": card.publisher,
                "section": card.section,
                "license": card.license,
                "url": card.source_url,
            }
        ):
            return VerifiedClaim(claim=claim, valid=False, reason="reference content withheld")
        where = f", {card.section}" if card.section else ""
        sentence = (
            f'{card.title} ({card.publisher}{where}) says: "{card.text}" '
            f"Source: {card.source_url}. This is general reference material, not an "
            "assessment of your own values."
        )
        return VerifiedClaim(claim=claim, valid=True, sentence=sentence)
    if claim.type == "window_summary":
        if card.kind != "daily_table" or claim.date is not None:
            return VerifiedClaim(
                claim=claim, valid=False, reason="window_summary needs a daily table and no date"
            )
        provider = f" ({card.provider})" if card.provider else ""
        window = _window_text(card.start_date, card.end_date)
        if not card.rows:
            sentence = f"No {card.metric} records{provider} between {window}."
        else:
            values = sum(r.quality == "value" for r in card.rows)
            unusable = sum(r.quality == "unusable" for r in card.rows)
            absent = sum(r.quality == "none" for r in card.rows)
            sentence = (
                f"For {card.metric}{provider} between {window}, the daily card shows "
                f"{values} day(s) with values, {unusable} with records but no reliable value, "
                f"and {absent} without records. See the card for each day's value in {card.unit} "
                f"({card.statistic}) and warnings."
            )
        return VerifiedClaim(claim=claim, valid=True, sentence=sentence)
    if claim.type == "available_series":
        if card.kind != "availability" or claim.date is not None:
            return VerifiedClaim(
                claim=claim,
                valid=False,
                reason="available_series needs an availability card and no date",
            )
        items = (
            "; ".join(f"{r['metric']} ({r['provider']}, {r['aggregation']})" for r in card.series)
            or "no recorded series"
        )
        return VerifiedClaim(
            claim=claim,
            valid=True,
            sentence=(
                f"Recorded series between {_window_text(card.start_date, card.end_date)}: {items}. "
                "This describes accessible records, not device wear or complete coverage."
            ),
        )
    if claim.type == "value_on_date":
        if card.kind != "daily_table" or claim.date is None:
            return VerifiedClaim(
                claim=claim, valid=False, reason="value_on_date needs a daily table and a date"
            )
        row = next((r for r in card.rows if r.date == claim.date), None)
        if row is None:
            return VerifiedClaim(claim=claim, valid=False, reason="date outside the card window")
        if row.value is None:
            return VerifiedClaim(claim=claim, valid=False, reason="no recorded value on that date")
        sentence = (
            f"On {row.date}, your {card.metric} ({card.provider}) was {row.display}, "
            f"{card.statistic}."
        )
        return VerifiedClaim(claim=claim, valid=True, sentence=sentence)
    if claim.type == "window_difference":
        if card.kind != "comparison" or card.baseline is None or card.current is None:
            return VerifiedClaim(
                claim=claim, valid=False, reason="window_difference needs a comparison card"
            )
        if card.direction == "unavailable":
            return VerifiedClaim(
                claim=claim, valid=False, reason="no difference available for this card"
            )
        b, c = card.baseline, card.current
        current_window = _window_text(c["start_date"], c["end_date"])
        baseline_window = _window_text(b["start_date"], b["end_date"])
        if card.direction == "unchanged":
            sentence = (
                f"Your {card.metric} ({card.provider}) was unchanged between {baseline_window} "
                f"and {current_window}: {c['display']} ({card.statistic})."
            )
        else:
            sentence = (
                f"Your {card.metric} ({card.provider}) was {card.direction} in {current_window} "
                f"than in {baseline_window}: {c['display']} versus {b['display']} "
                f"({card.statistic}; {c['days_with_data']} of {c['days_in_window']} and "
                f"{b['days_with_data']} of {b['days_in_window']} days with data)."
            )
        return VerifiedClaim(claim=claim, valid=True, sentence=sentence)
    # missing_data: three states are distinguished, never collapsed into "no records".
    if card.kind == "daily_table":
        provider = f" ({card.provider})." if card.provider else "."
        if claim.date is not None:
            row = next((r for r in card.rows if r.date == claim.date), None)
            if row is None and not card.rows and _inside(card, _calendar_date(claim.date)):
                # A "no records" card has no rows; any date inside its window has no records.
                sentence = f"No {card.metric} records on {claim.date}" + provider
                return VerifiedClaim(claim=claim, valid=True, sentence=sentence)
            if row is None:
                return VerifiedClaim(
                    claim=claim, valid=False, reason="date outside the card window"
                )
            if row.quality == "value":
                return VerifiedClaim(
                    claim=claim, valid=False, reason="that date has a recorded value"
                )
            if row.quality == "unusable":
                return VerifiedClaim(
                    claim=claim, valid=True, sentence=_row_sentence_unusable(card, row)
                )
            sentence = f"No {card.metric} records on {row.date}" + provider
            return VerifiedClaim(claim=claim, valid=True, sentence=sentence)
        if any(r.quality == "value" for r in card.rows):
            return VerifiedClaim(claim=claim, valid=False, reason="the window has recorded values")
        unusable = [r for r in card.rows if r.quality == "unusable"]
        if unusable:
            sentence = (
                f"No reliable {card.metric} values between {card.start_date} and {card.end_date}"
                f"{provider[:-1]}: records exist on {len(unusable)} day(s) but could not be "
                "summarised."
            )
            return VerifiedClaim(claim=claim, valid=True, sentence=sentence)
        sentence = (
            f"No {card.metric} records between {card.start_date} and {card.end_date}" + provider
        )
        return VerifiedClaim(claim=claim, valid=True, sentence=sentence)
    if card.kind == "comparison":
        if card.direction != "unavailable":
            return VerifiedClaim(
                claim=claim, valid=False, reason="the comparison has values on both sides"
            )
        sentence = f"Not enough {card.metric} data to compare the two windows."
        return VerifiedClaim(claim=claim, valid=True, sentence=sentence)
    if card.kind == "availability":
        if card.series:
            return VerifiedClaim(claim=claim, valid=False, reason="the window has recorded series")
        sentence = f"No recorded series between {card.start_date} and {card.end_date}."
        return VerifiedClaim(claim=claim, valid=True, sentence=sentence)
    return VerifiedClaim(claim=claim, valid=False, reason="unsupported card kind")


def render_explanation(
    findings: FindingsParams, cards: list[Card]
) -> tuple[str, list[VerifiedClaim], bool]:
    """Returns (text, verified claims, all_valid). Any invalid claim yields the fallback text."""
    verified = [
        verify_claim(claim, cards)
        if isinstance(claim, Claim)
        else VerifiedClaim(claim=None, raw=claim, valid=False, reason="unparseable finding")
        for claim in findings.claims
    ]
    if all(v.valid for v in verified):
        return " ".join(v.sentence for v in verified if v.sentence), verified, True
    return FALLBACK_EXPLANATION, verified, False


__all__ = [
    "Claim",
    "FindingsParams",
    "VerifiedClaim",
    "FALLBACK_EXPLANATION",
    "render_explanation",
    "verify_claim",
    "format_value",
]
