"""Check explicit durations against seconds-based facts, without whitelisting new digits.

Supports Arabic numerals with English/Chinese hour, minute and second labels. This
is a dimensional/arithmetic diagnostic, not verification of dates, attribution or trends.
"""

import re
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
HOUR = r"(?:hours?|hrs?|h)(?![a-z])|\u5c0f\u65f6|\u5c0f\u6642"
MINUTE = r"(?:minutes?|mins?|min)(?![a-z])|\u5206\u949f|\u5206\u9418"
SECOND = r"(?:seconds?|secs?|s)(?![a-z])|\u79d2"
JOIN = r"\s*(?:and\s+)?"
DURATION = re.compile(
    rf"(?<![\d.])(?:"
    rf"(?P<h>{NUMBER})\s*(?:{HOUR})"
    rf"(?:{JOIN}(?P<hm>{NUMBER})\s*(?:{MINUTE}))?"
    rf"(?:{JOIN}(?P<hs>{NUMBER})\s*(?:{SECOND}))?"
    rf"|(?P<m>{NUMBER})\s*(?:{MINUTE})"
    rf"(?:{JOIN}(?P<ms>{NUMBER})\s*(?:{SECOND}))?"
    rf"|(?P<s>{NUMBER})\s*(?:{SECOND}))",
    re.I,
)
SUMMARY_FIELDS = (
    "minimum",
    "maximum",
    "mean_of_available_daily_values",
    "first_to_last_change",
    "absolute_change",
)
CONVERSION_TOLERANCE_SECONDS = Decimal(120)


def seconds_values(facts: dict) -> set[Decimal]:
    """Use dimensional measurement fields only, never dates/counts/warning prose."""
    values = []
    for source in facts.get("sources", []):
        if source.get("unit") != "s":
            continue
        values.extend(d.get("value") for d in source.get("days", []) if d.get("quality") == "value")
        summary = source.get("recorded_values_summary", {})
        values.extend(summary.get(k) for k in SUMMARY_FIELDS)
        for key in ("baseline", "current"):
            value = source.get(key)
            if isinstance(value, dict):
                values.append(value.get("value"))
        values.append(source.get("difference"))
        values.extend(d.get("value") for d in source.get("readings", []))
        values.extend(source.get("model_estimate", {}).values())
        values.extend(
            source.get("comparison_to_estimate", {}).get("absolute_distances", {}).values()
        )
    result = set()
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            number = Decimal(str(value))
            if number.is_finite():
                result.add(number)
        except InvalidOperation:
            continue
    return result


def checked_duration_text(answer: str, facts: dict) -> tuple[str, list[str], list[str]]:
    """Mask duration spans only after checking the whole quantity in seconds.

    A composite minute/second component must be in [0, 60). Exact conversions need
    no qualifier. Hour/minute conversions allow an absolute 120-second tolerance;
    zero and sign changes are never introduced by that tolerance;
    precision is determined by the last displayed component. Seconds retain the
    existing numeric checker's rounding convention. Invalid spans are returned even
    when their component digits happen to occur elsewhere in the evidence.
    """
    pool = seconds_values(facts)
    found, invalid = [], []

    def check(match):
        span = match.group()
        found.append(span)
        parts = [
            (k, Decimal(v.replace(",", ""))) for k, v in match.groupdict().items() if v is not None
        ]
        factors = {"h": 3600, "hm": 60, "hs": 1, "m": 60, "ms": 1, "s": 1}
        valid = all(Decimal(0) <= v < 60 for k, v in parts if k in {"hm", "hs", "ms"})
        # A fraction in a larger component plus a smaller component is ambiguous.
        valid &= all(v == v.to_integral_value() for _, v in parts[:-1])
        # Signed composites are not supported; reject rather than adding a positive remainder.
        valid &= len(parts) == 1 or parts[0][1] >= 0
        total = sum(v * factors[k] for k, v in parts)
        key, last = parts[-1]
        quantum = Decimal(1).scaleb(min(0, last.as_tuple().exponent))
        try:
            matched = (
                total in pool
                or (
                    any(k in {"h", "m"} for k, _ in parts)
                    and total != 0
                    and any(
                        v != 0
                        and (v > 0) == (total > 0)
                        and abs(total - v) <= CONVERSION_TOLERANCE_SECONDS
                        for v in pool
                    )
                )
                or (
                    factors[key] == 1
                    and any(
                        (v / factors[key]).quantize(quantum, rounding=ROUND_HALF_EVEN)
                        * factors[key]
                        == total
                        for v in pool
                    )
                )
            )
        except InvalidOperation:
            matched = False
        if not valid or not matched:
            invalid.append(span)
        return " "

    return DURATION.sub(check, answer), found, invalid
