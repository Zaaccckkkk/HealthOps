"""Bounded English/Chinese screening, not a universal injection detector.

Never use a screening result as authorization. Identity, tool allowlists and scope
checks remain server-owned even when this filter permits a request. Only reason
codes are logged; prompts, credentials and health records are never logged here.
"""

import logging
import re
import unicodedata

VERSION = "assistant-guardrails-v3-sensitive-output"
logger = logging.getLogger(__name__)

LANGUAGE_REFUSAL = "Only English and Chinese are supported. Please rephrase in English or Chinese."


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return " ".join(text.split())


def intake_issue(text: str) -> str | None:
    """Apply the input-only script policy, then security screening.

    Latin/Han letters are allowed; other scripts are refused even inside quotes or
    mixed-language input. Digits, punctuation and emoji are allowed. This is not
    language detection: Latin-script languages and Han-only Japanese still need
    the router's language policy. Normalization never rewrites the forwarded text.
    """
    for char in normalize(text):
        if not unicodedata.category(char).startswith("L"):
            continue
        name = unicodedata.name(char, "")
        if not name.startswith(
            ("LATIN ", "CJK UNIFIED IDEOGRAPH-", "CJK COMPATIBILITY IDEOGRAPH-")
        ):
            return "unsupported_language"
    return input_issue(text)


# Require an action and a protected target, not words like "prompt" alone.
_RULES = (
    (
        "instruction_override",
        r"\b(?:ignore|disregard|override|forget|bypass|disable)\b.{0,60}"
        r"\b(?:instructions?|system prompt|safety rules?|guardrails?|security checks?|"
        r"permissions?|access controls?|previous rules|all rules)\b|"
        r"(?:\u5ffd\u7565|\u65e0\u89c6|\u7ed5\u8fc7|\u5173\u95ed|\u8986\u76d6).{0,30}"
        r"(?:\u6307\u4ee4|\u7cfb\u7edf\u63d0\u793a|\u5b89\u5168\u89c4\u5219|"
        r"\u6743\u9650|\u62a4\u680f|\u5b89\u5168\u68c0\u67e5|\u6240\u6709\u89c4\u5219)",
    ),
    (
        "role_spoofing",
        r"<\s*/?\s*(?:system|developer|assistant)\b|"
        r"\[/?INST\]|\[im_start\]|<\|(?:im_start|start_header_id)\|>|"
        r"\b(?:system|developer)\s*:|"
        r"\b(?:you are now|act as|pretend to be)\s+(?:an?\s+)?"
        r"(?:unrestricted|unfiltered|DAN|system administrator)\b|"
        r"(?:\u4f60\u73b0\u5728\u662f|\u626e\u6f14)(?:\u4e00\u4e2a)?"
        r"(?:\u65e0\u9650\u5236|\u65e0\u5ba1\u67e5|\u7cfb\u7edf\u7ba1\u7406\u5458)",
    ),
    (
        "secret_extraction",
        r"\b(?:reveal|print|expose|leak|show|give|return|repeat|output|send|paste|upload|provide)\b.{0,55}"
        r"\b(?:system prompt|developer (?:prompt|instructions)|API key|access token|"
        r"credentials|password|environment variables|hidden instructions)\b|"
        r"(?:\u663e\u793a|\u8f93\u51fa|\u6cc4\u9732|\u6253\u5370|\u544a\u8bc9\u6211|"
        r"\u590d\u8ff0|\u53d1\u9001|\u7c98\u8d34|\u63d0\u4f9b).{0,25}(?:\u7cfb\u7edf\u63d0\u793a|\u5bc6\u94a5|\u5bc6\u7801|"
        r"\u73af\u5883\u53d8\u91cf|\u8bbf\u95ee\u4ee4\u724c|\u9690\u85cf\u6307\u4ee4)",
    ),
    (
        "unauthorized_data_request",
        r"\b(?:show|fetch|read|query|export|give|retrieve)\b.{0,50}"
        r"\b(?:other (?:users?|people)|another (?:user|account)|all users|someone else)"
        r".{0,35}\b(?:data|records?|history)\b|"
        r"(?:\u67e5\u8be2|\u663e\u793a|\u8bfb\u53d6|\u5bfc\u51fa|\u7ed9\u6211).{0,20}"
        r"(?:\u5176\u4ed6\u7528\u6237|\u522b\u4eba|\u6240\u6709\u7528\u6237|"
        r"\u53e6\u4e00\u4e2a\u8d26\u6237).{0,15}(?:\u6570\u636e|\u8bb0\u5f55|\u5386\u53f2)",
    ),
    (
        "unauthorized_execution",
        r"\b(?:execute|run|invoke|call)\s+(?:the\s+)?(?:SQL|shell|bash|run_shell|"
        r"execute_sql|http_post)\b|"
        r"(?:\u6267\u884c|\u8fd0\u884c|\u8c03\u7528)\s*(?:SQL|shell|bash|run_shell|execute_sql)",
    ),
    (
        "encoded_instruction",
        r"\bdecode\b(?:(?!\b(?:not|never|don't|without)\b)[^.!?]){0,100}"
        r"\b(?:execute|obey|follow)\b|"
        r"\u89e3\u7801(?:(?!\u4e0d\u8981|\u4e0d\u80fd|\u4e0d\u6267\u884c)"
        r"[^\u3002\uff01\uff1f]){0,40}(?:\u6267\u884c|\u9075\u5faa)",
    ),
)


def _clause_gaps(pattern: str) -> str:
    # Do not connect "show my steps" to "never reveal credentials" in a
    # different clause. Each subsequent action is still screened independently.
    gap = (
        r"(?:(?!\b(?:and|but|never|not|don't|cannot|can't|without)\b|"
        r"\u4e0d\u8981|\u4e0d\u80fd|\u5e76|\u7136\u540e|\u4f46\u662f)"
        r"[^.,;:!?\u3002\uff0c\uff1b\uff1a\uff01\uff1f])"
    )
    return re.sub(r"\.\{0,(\d+)\}", lambda m: gap + "{0," + m[1] + "}?", pattern)


RULES = tuple((code, re.compile(_clause_gaps(pattern), re.I)) for code, pattern in _RULES)

# A narrow whole-request exception for discussing a quoted phrase. Never strip
# arbitrary quotes: an attacker can quote an instruction and ask us to obey it.
_QUOTES = re.compile(r'"[^"\n]{1,300}"|\u201c[^\u201d\n]{1,300}\u201d|\'[^\'\n]{1,300}\'')
_DISCUSSION = re.compile(
    r"(?:what does QUOTE mean|explain (?:the phrase )?QUOTE|"
    r"is QUOTE (?:a prompt injection|safe)|"
    r"QUOTE\u662f\u4ec0\u4e48\u610f\u601d|\u89e3\u91ca(?:\u4e00\u4e0b)?QUOTE|"
    r"QUOTE\u662f\u63d0\u793a\u6ce8\u5165\u5417)[?.!\u3002\uff1f]*",
    re.I,
)


def input_issue(text: str) -> str | None:
    """Return a safe reason code. Do not alter the text forwarded to the router."""
    return _pattern_issue(text, RULES)


def _pattern_issue(text: str, rules) -> str | None:
    text = normalize(text)
    if _DISCUSSION.fullmatch(_QUOTES.sub("QUOTE", text)):
        return None
    for code, pattern in rules:
        offset = 0
        while match := pattern.search(text, offset):
            # Search overlapping starts: a negated action must not hide another
            # imperative swallowed by the same bounded pattern match.
            offset = match.start() + 1
            # Explicit negation is not a request to perform the prohibited action.
            prefix = text[: match.start()]
            if re.search(
                r"(?:\bdo not |\bdon't |\bnever |\bcannot |\bcan't |\bwill not |\bwon't |"
                r"\u4e0d\u8981|\u8bf7\u52ff|\u7981\u6b62|\u4e0d\u80fd|\u4e0d\u4f1a)$",
                prefix,
                re.I,
            ):
                continue
            return code
    return None


def output_issue(answer: str, protected_prompts: tuple[str, ...] = ()) -> str | None:
    """Broad first-draft feedback; the final gate uses sensitive_output_issue."""
    return input_issue(answer) or sensitive_output_issue(answer, protected_prompts)


def sensitive_output_issue(answer: str, protected_prompts: tuple[str, ...] = ()) -> str | None:
    """Final gate: credential requests/leaks and protected prompt disclosure only.

    Quality findings and other instruction patterns are advisory draft feedback.
    This bounded screen cannot recognize every secret or paraphrased prompt leak.
    """
    text = normalize(answer)
    issue = _pattern_issue(text, tuple(rule for rule in RULES if rule[0] == "secret_extraction"))
    if issue:
        return issue
    if re.search(
        r"\b(?:api[_ -]?key|access[_ -]?token|password|secret)\s*[:=]\s*\S+|"
        r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----|\bBearer\s+[A-Za-z0-9_.-]{12,}|"
        r"(?:\u5bc6\u7801|\u5bc6\u94a5|\u4ee4\u724c)\s*[:=]\s*\S+",
        text,
        re.I,
    ):
        return "credential_like_output"
    folded = text.casefold()
    for prompt in protected_prompts:
        protected = normalize(prompt).casefold()
        if any(protected[i : i + 80] in folded for i in range(0, len(protected) - 79, 40)):
            return "prompt_disclosure"
    return None


def record_block(stage: str, reason: str) -> None:
    logger.info("assistant_guardrail version=%s stage=%s reason=%s", VERSION, stage, reason)


def refusal(question: str, reason: str | None = None) -> str:
    if reason == "unsupported_language":
        return LANGUAGE_REFUSAL
    if re.search(r"[\u3400-\u9fff]", question):
        return (
            "\u6211\u4e0d\u80fd\u7ed5\u8fc7\u5b89\u5168\u6216\u6743\u9650\u9650\u5236\uff0c"
            "\u4e5f\u4e0d\u80fd\u63d0\u4f9b\u51ed\u636e\u6216\u4ed6\u4eba\u6570\u636e\u3002"
            "\u8bf7\u76f4\u63a5\u8be2\u95ee\u4f60\u81ea\u5df1\u7684\u5065\u5eb7\u8bb0\u5f55\u3002"
        )
    return (
        "I cannot bypass safety or access controls, disclose credentials, or access someone "
        "else's data. Please ask directly about your own recorded health data."
    )
