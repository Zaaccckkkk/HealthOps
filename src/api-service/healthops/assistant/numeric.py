"""Numeric-consistency diagnostic: are the numbers in an answer present in the evidence?"""

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from healthops.assistant.contracts import NumericConsistency

# Dates and times are not measurements; they are stripped before number extraction.
_DATE_LIKE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?"
)
_NUMBER = re.compile(r"(?<![\w.])[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])[-+]?\d+(?:\.\d+)?")
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
# "September 21", "Sep 21, 2026", "21 September 2026", "October 2nd": calendar text, not data.
_PROSE_DATE = re.compile(
    rf"\b{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?|\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}(?:\s+\d{{4}})?",
    re.IGNORECASE,
)


def numbers_in_text(text: str) -> list[str]:
    cleaned = _PROSE_DATE.sub(" ", _DATE_LIKE.sub(" ", text))
    return [match.replace(",", "") for match in _NUMBER.findall(cleaned)]


def _collect(value: Any, out: set[Decimal]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        out.add(Decimal(str(value)))
    elif isinstance(value, str):
        try:
            if _NUMBER.fullmatch(value):
                out.add(Decimal(value))
        except InvalidOperation:
            pass
    elif isinstance(value, dict):
        for item in value.values():
            _collect(item, out)
    elif isinstance(value, list):
        for item in value:
            _collect(item, out)


def _matches(number: Decimal, evidence: set[Decimal]) -> bool:
    if number in evidence:
        return True
    places = -number.as_tuple().exponent if number.as_tuple().exponent < 0 else 0
    quantum = Decimal(1).scaleb(-places)
    # An answer may round a tool value: 60.5 matches 60.500000, 61 matches 60.5 (half-even).
    return any(candidate.quantize(quantum) == number for candidate in evidence)


def check(answer: str, evidence: list[dict[str, Any]]) -> NumericConsistency:
    found = numbers_in_text(answer)
    if not found:
        return NumericConsistency(status="not_applicable", numbers_in_answer=[], unmatched=[])
    pool: set[Decimal] = set()
    _collect(evidence, pool)
    unmatched = []
    for text in found:
        try:
            number = Decimal(text)
        except InvalidOperation:
            unmatched.append(text)
            continue
        if not _matches(number, pool):
            unmatched.append(text)
    return NumericConsistency(
        status="unmatched" if unmatched else "consistent",
        numbers_in_answer=found,
        unmatched=unmatched,
    )
