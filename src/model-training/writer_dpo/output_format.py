"""Conservative formatting of writer prose, never semantic repair.

Keep model outputs and evidence identifiers intact. Call this only on the parsed
answer field; the result and changes can be saved alongside the original answer.
"""

import json
import re
from dataclasses import dataclass
from decimal import InvalidOperation

from .evidence_facts import date_text, number
from .presentation import duration_text

VERSION = "writer-output-format-v1"
PROTECTED = re.compile(r"```[\s\S]*?```|`[^`]*`|https?://\S+|\be\d+\.[\w.-]+")
ISO_DATE = re.compile(r"(?<![A-Za-z0-9_./-])\d{4}-\d{2}-\d{2}(?![A-Za-z0-9_/-])")
SECONDS = re.compile(
    r"(?<![A-Za-z0-9_.,])(?P<value>-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"\s*(?:seconds?\b|secs?\b|s\b|\u79d2(?:\u949f)?)(?!\s*(?:/|per\b))",
    re.I,
)


@dataclass(frozen=True)
class FormattedAnswer:
    answer: str
    changes: tuple[dict, ...]
    warnings: tuple[str, ...]


def latest_user_text(context: str) -> str:
    """Ignore old assistant clarification text when detecting a format override."""
    try:
        payload = json.loads(context)
    except (ValueError, TypeError):
        return context
    if isinstance(payload, dict):
        dialogue = payload.get("user_dialogue", [])
        for turn in reversed(dialogue):
            if turn.get("role") == "user":
                return str(turn.get("content", ""))
        return str(payload.get("user_request", context))
    return context


def _seconds_values(facts):
    """Allow only typed duration quantities, including supplied differences."""
    values = set()

    def add(value):
        if value is not None:
            try:
                values.add(number(value))
            except (ValueError, InvalidOperation):
                pass

    for source in facts.get("sources", []):
        if source.get("unit") != "s":
            continue
        for key in ("days", "readings"):
            for row in source.get(key, []):
                add(row.get("value"))
        for key in ("baseline", "current"):
            add(source.get(key, {}).get("value"))
        add(source.get("difference"))
        for key in ("model_estimate", "recorded_values_summary"):
            for field, value in source.get(key, {}).items():
                if field in {
                    "q05",
                    "q50",
                    "q95",
                    "minimum",
                    "maximum",
                    "mean_of_available_daily_values",
                    "first_to_last_change",
                    "absolute_change",
                }:
                    add(value)
        for value in (
            source.get("comparison_to_estimate", {}).get("absolute_distances", {}).values()
        ):
            add(value)
    return values


def format_answer(answer: str, facts: dict, language: str, question: str = "") -> FormattedAnswer:
    """Normalize explicit literals; never infer a date, unit, source, or missing fact."""
    question = latest_user_text(question)
    keep_iso = bool(re.search(r"\bISO\b|YYYY-MM-DD", question, re.I))
    # Conservative: any seconds reference keeps raw seconds. A false positive
    # skips conversion rather than silently contradicting an explicit request.
    keep_seconds = bool(re.search(r"\bseconds?\b|\bsecs?\b|\u79d2", question, re.I))
    seconds = _seconds_values(facts)
    source_text = json.dumps(facts.get("sources", []), ensure_ascii=True) + " " + question
    has_apple = bool(re.search(r"healthkit|(?<![A-Za-z])apple(?![A-Za-z])", source_text, re.I))
    has_google = bool(
        re.search(r"google_health|(?<![A-Za-z])google(?![A-Za-z])", source_text, re.I)
    )
    changes, warnings = [], []

    def changed(kind, before, after):
        if before != after:
            changes.append({"kind": kind, "before": before, "after": after})
        return after

    def dates(match):
        before = match.group()
        try:
            return changed("date", before, date_text(before, language))
        except ValueError:
            warnings.append("invalid_calendar_date_left_unchanged")
            return before

    def durations(match):
        value = number(match.group("value").replace(",", ""))
        if value not in seconds:
            warnings.append("unsupported_seconds_left_unchanged")
            return match.group()
        return changed("duration", match.group(), duration_text(value, language))

    def prose(text):
        text = re.sub(
            r"(?<![A-Za-z0-9_.])(?:Apple\s+)?HealthKit(?![A-Za-z0-9_])",
            lambda m: changed("source", m.group(), "Apple Health"),
            text,
            flags=re.I,
        )
        for enabled, name, products in [
            (has_apple, "Apple", "Watch|Inc|Music|Pay|ID|Store|Silicon"),
            (has_google, "Google", "Cloud|Fit|Pixel|Play|Maps|Drive|Search"),
        ]:
            if enabled:
                pattern = (
                    rf"(?<![A-Za-z0-9_.]){name}(?:\s+Health)?"
                    rf"(?![A-Za-z0-9_]|\s+(?:Health\b|{products}\b))"
                )
                text = re.sub(
                    pattern,
                    lambda m: changed("source", m.group(), name + " Health"),
                    text,
                    flags=re.I,
                )
        if not keep_iso:
            text = ISO_DATE.sub(dates, text)
        if not keep_seconds:
            text = SECONDS.sub(durations, text)
            # Avoid doubling a qualifier already present before raw seconds.
            text = re.sub(r"\babout\s+about\s+", "about ", text, flags=re.I)
            text = text.replace("\u7ea6\u7ea6", "\u7ea6")
        else:

            def exact_seconds(match):
                literal = match.group("literal")
                amount = SECONDS.fullmatch(literal)
                if amount and number(amount.group("value").replace(",", "")) in seconds:
                    return changed("exact_seconds", match.group(), literal)
                return match.group()

            text = re.sub(
                r"(?:\babout\s+|\bapproximately\s+|\u7ea6\s*)"
                r"(?P<literal>" + SECONDS.pattern + ")",
                exact_seconds,
                text,
                flags=re.I,
            )
        return text

    parts, cursor = [], 0
    for match in PROTECTED.finditer(answer):
        parts.extend((prose(answer[cursor : match.start()]), match.group()))
        cursor = match.end()
    parts.append(prose(answer[cursor:]))
    return FormattedAnswer("".join(parts), tuple(changes), tuple(sorted(set(warnings))))
