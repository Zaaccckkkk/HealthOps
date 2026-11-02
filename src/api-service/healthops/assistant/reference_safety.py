"""Conservative reference screening, not a general prompt-injection classifier.

Retrieved text is untrusted even when its provenance is known. Known instruction/credential
patterns are withheld, never rewritten into an apparently faithful quotation. The model
receives only evidence handles; human-readable passages stay on the server for quotation.
"""

import copy
import re
import unicodedata
from urllib.parse import urlsplit

BLOCKED = "reference_content_blocked"
SOURCE_ONLY = "Reference quotes are matched to their source, not certified safe or medically valid."
_FIELDS = ("text", "source_title", "publisher", "section", "license", "source_id")
_PATTERNS = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"\b(ignore|disregard|override|forget)\b.{0,70}\b(instructions?|rules?|prompts?|polic(?:y|ies))\b",
        r"\b(system|developer|assistant)\s*(?:prompt\s*)?[:>]",
        r"<\s*/?\s*(system|assistant|developer)\b|\[/?INST\]|\[im_start\]",
        r"\b(reveal|print|expose|leak)\b.{0,70}"
        r"\b(system prompt|secrets?|credentials?|tokens?|api keys?)\b",
        r"\b(paste|enter|send|upload|provide)\b.{0,70}"
        r"\b(password|access token|api key|secret key|credentials)\b",
        r"\b(call|invoke|execute|run)\b.{0,40}\b(state_findings|run_shell|execute_sql|http_post)\b",
        r"\u5ffd\u7565.{0,20}(\u6307\u4ee4|\u89c4\u5219|\u63d0\u793a)|\u6cc4\u9732.{0,20}(\u5bc6\u94a5|\u5bc6\u7801|\u63d0\u793a\u8bcd)|"
        r"(\u53d1\u9001|\u7c98\u8d34|\u63d0\u4f9b|\u8f93\u5165).{0,20}(\u5bc6\u7801|\u5bc6\u94a5|\u4ee4\u724c|token)",
    )
)


def suspicious_text(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Cf")
    normalized = " ".join(normalized.split())
    return any(pattern.search(normalized) for pattern in _PATTERNS)


def reference_allowed(reference: dict) -> bool:
    if any(suspicious_text(str(reference.get(key) or "")) for key in _FIELDS):
        return False
    try:
        url = urlsplit(reference.get("url") or "")
        return (
            url.scheme in {"http", "https"}
            and bool(url.hostname)
            and not (url.username or url.password)
        )
    except ValueError:
        return False


def screen_result(result: dict) -> dict:
    """Copy before filtering so no stored corpus or caller-owned fixture is mutated."""
    screened = copy.deepcopy(result)
    original = screened.get("references") or []
    screened["references"] = [r for r in original if reference_allowed(r)]
    if len(screened["references"]) != len(original):
        screened["warnings"] = list(dict.fromkeys([*screened.get("warnings", []), BLOCKED]))
    return screened
