"""Writer-specific date validation before numeric membership checks.

Recognized calendar expressions must name a date present in the fact packet; only then
are they removed from measurement checks. This is not general natural-language verification.
"""

import json
import re
from datetime import date

from healthops.assistant import numeric
from healthops.assistant.contracts import NumericConsistency

ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
ZH = re.compile(r"(?:(\d{4})\u5e74)?(\d{1,2})\u6708(\d{1,2})[\u65e5\u53f7]")
MONTHS = {
    name: n
    for n, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}
MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
)
EN = re.compile(rf"\b({MONTH})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b", re.I)
EN_REVERSED = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTH})(?:\s+(\d{{4}}))?\b", re.I)
EN_SHARED_MONTH = re.compile(
    rf"\b({MONTH})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:to|through|and|[-\u2013\u2014])\s*"
    r"(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b",
    re.I,
)
ZH_SHARED_MONTH = re.compile(
    r"(?:(\d{4})\u5e74)?(\d{1,2})\u6708(\d{1,2})[\u65e5\u53f7]\s*"
    r"(?:\u81f3|\u5230|\u548c|\u4e0e|\u3001|[-\u2013\u2014])\s*(\d{1,2})[\u65e5\u53f7]"
)


def _calendar_text(answer: str, facts: dict) -> str | None:
    """Return calendar-free text, or None for an invalid/unsupported/ambiguous date."""
    allowed = {date.fromisoformat(d) for d in ISO.findall(json.dumps(facts))}
    invalid = False

    def remove(year, month, day):
        nonlocal invalid
        matches = [
            d
            for d in allowed
            if d.month == int(month) and d.day == int(day) and (year is None or d.year == int(year))
        ]
        # With no year, more than one possible year must not silently resolve.
        if len(matches) != 1:
            invalid = True
        return " "

    def iso(match):
        y, m, d = match.group().split("-")
        return remove(y, m, d)

    def shared_month(year, month, first, last):
        nonlocal invalid
        # Both endpoints must exist in the same unambiguous year. Do not turn
        # an unsupported day into a number-free sentence or infer missing days.
        years = [
            y
            for y in {d.year for d in allowed}
            if (year is None or y == int(year))
            and all(
                any(d.year == y and d.month == int(month) and d.day == int(day) for d in allowed)
                for day in (first, last)
            )
        ]
        if len(years) != 1:
            invalid = True
        return " "

    text = ISO.sub(iso, answer)
    text = EN_SHARED_MONTH.sub(
        lambda m: shared_month(m[4], MONTHS[m[1][:3].lower()], m[2], m[3]), text
    )
    text = ZH_SHARED_MONTH.sub(lambda m: shared_month(m[1], m[2], m[3], m[4]), text)
    text = ZH.sub(lambda m: remove(m[1], m[2], m[3]), text)
    text = EN.sub(lambda m: remove(m[3], MONTHS[m[1][:3].lower()], m[2]), text)
    text = EN_REVERSED.sub(lambda m: remove(m[3], MONTHS[m[2][:3].lower()], m[1]), text)
    if invalid:
        return None
    return text


def checked_numeric_text(answer: str, facts: dict) -> str | None:
    """Compatibility entry point for calendar validation and legacy digit parsing."""
    text = _calendar_text(answer, facts)
    return None if text is None else re.sub(r"[\u3400-\u9fff]", " ", text)


def numeric_diagnostic(answer: str, facts: dict) -> NumericConsistency:
    from healthops.assistant.duration_checks import checked_duration_text

    text = _calendar_text(answer, facts)
    if text is None:
        return NumericConsistency(
            status="unmatched", numbers_in_answer=[], unmatched=["unsupported_date"]
        )
    text, durations, invalid = checked_duration_text(text, facts)
    result = numeric.check(re.sub(r"[\u3400-\u9fff]", " ", text), [facts])
    return NumericConsistency(
        status="unmatched"
        if invalid or result.unmatched
        else ("consistent" if durations or result.numbers_in_answer else "not_applicable"),
        numbers_in_answer=result.numbers_in_answer + durations,
        unmatched=result.unmatched + invalid,
    )
