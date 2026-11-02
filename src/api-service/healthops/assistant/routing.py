"""Bounded intent routing. Model proposals are validated before any data tool runs."""

import re
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from healthops.assistant.contracts import (
    ClarificationField,
    ClarificationForm,
    ClarificationOption,
    TimeSemantics,
)
from healthops.assistant.model import ChatModel, ModelError
from healthops.schemas import Metric
from healthops.tools.local_days import LocalDayWindow, ToolInputError


class WindowProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phrase: str = Field(max_length=120, description="Exact time phrase from a USER message.")
    kind: Literal[
        "today",
        "yesterday",
        "last_night",
        "past_7_days",
        "past_15_days",
        "past_30_days",
        "previous_7_days",
        "previous_30_days",
        "this_week",
        "last_week",
        "week_before_last",
        "three_weeks_ago",
        "explicit",
    ]
    start_date: date | None = None
    end_date: date | None = None


class RouteProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route: Literal["ready", "needs_clarification", "unsupported"]
    intent: Literal["summary", "comparison", "availability", "reference", "unknown"]
    metrics: list[Metric] = Field(default_factory=list, max_length=8)
    window: WindowProposal | None = None
    baseline: WindowProposal | None = None
    language: Literal["en", "zh"] = "en"
    reason: Literal[
        "medical_advice", "other_person", "invented_value_requested", "out_of_scope"
    ] = "out_of_scope"


class RouterArguments(BaseModel):
    """Flat model-facing contract; dates remain server-resolved, not model-generated."""

    model_config = ConfigDict(extra="forbid")
    decision: Literal["ready", "needs_clarification", "unsupported"] = Field(
        description="REQUIRED routing decision, NOT a task name. Always include this field."
    )
    task: Literal["summary", "comparison", "availability", "reference", "unknown"] = Field(
        description="What to do. 'unsupported' is NOT a task; it belongs in decision."
    )
    metrics: list[Metric] = Field(
        max_length=8,
        description="Only metrics explicitly named by the user. Use [] when unspecified; "
        "never expand wellbeing or sleep quality into guessed metrics.",
    )
    time_phrase: str = Field(
        max_length=120,
        description="Exact USER quote for the current window, e.g. 'yesterday' or "
        "'2026-09-02 to 2026-09-04'. Use empty string for missing/ambiguous time. "
        "Never convert recently/lately into a number of days.",
    )
    baseline_phrase: str = Field(
        max_length=120,
        description="Exact USER quote for the earlier comparison window; empty otherwise. "
        "'usual' is ambiguous, so use empty string.",
    )
    language: Literal["en", "zh"] = Field(description="Language of the user's messages.")
    reason: Literal[
        "none", "medical_advice", "other_person", "invented_value_requested", "out_of_scope"
    ] = Field(
        description="Use none for ready/clarification. For unsupported give the refusal reason."
    )

    @model_validator(mode="after")
    def refusal_reason(self):
        if (self.decision == "unsupported") == (self.reason == "none"):
            raise ValueError("Unsupported requires a reason; other decisions require none.")
        return self

    def proposal(self):
        return RouteProposal(
            route=self.decision,
            intent=self.task,
            metrics=self.metrics,
            window=quoted_window(self.time_phrase),
            baseline=quoted_window(self.baseline_phrase),
            language=self.language,
            reason=self.reason if self.reason != "none" else "out_of_scope",
        )


def router_tool_schema():
    """Inline enum definitions so model servers need not interpret JSON Schema references."""
    schema = RouterArguments.model_json_schema()
    definitions = schema.pop("$defs", {})

    def inline(value):
        if isinstance(value, list):
            return [inline(item) for item in value]
        if isinstance(value, dict):
            if "$ref" in value:
                return inline(definitions[value["$ref"].split("/")[-1]])
            return {key: inline(item) for key, item in value.items()}
        return value

    return {
        "type": "function",
        "function": {
            "name": "route_request",
            "description": "Return ALL seven required fields: decision, task, metrics, "
            "time_phrase, "
            "baseline_phrase, language, reason. Select a decision before extracting task scope.",
            "parameters": inline(schema),
        },
    }


class DateWindow(BaseModel):
    start_date: date
    end_date: date


class RequestSpec(BaseModel):
    intent: str
    metrics: list[Metric]
    window: DateWindow | None
    baseline: DateWindow | None
    language: Literal["en", "zh"]
    unresolved_fields: list[str]
    router_version: str = "2026-11-01.1"


ALIASES = {
    "today": r"today|\u4eca\u5929",
    "yesterday": r"yesterday|\u6628\u5929",
    "last_night": r"last night|\u6628\u665a|\u6628\u5929\u665a\u4e0a",
    "past_7_days": (
        r"(?:past|last|recent) (?:7|seven) days|(?:7|seven) days|\u6700"
        r"\u8fd1[\u4e037]\u5929|\u8fc7\u53bb[\u4e037]\u5929|[\u4e037]\u5929"
    ),
    "past_15_days": (
        r"(?:past|last) (?:15|fifteen) days|(?:15|fifteen) days|\u6700"
        r"\u8fd1(?:15|\u5341\u4e94)\u5929|(?:15|\u5341\u4e94)\u5929"
    ),
    "past_30_days": (
        r"(?:past|last) (?:30|thirty) days|(?:30|thirty) days|\u6700\u8fd1("
        r"?:30|\u4e09\u5341)\u5929|(?:30|\u4e09\u5341)\u5929"
    ),
    "this_week": r"this week|\u672c\u5468|\u8fd9\u5468",
    "last_week": r"last week|\u4e0a\u5468",
    "previous_7_days": r"previous (?:7|seven) days|\u4e4b\u524d[\u4e037]\u5929",
    "previous_30_days": r"previous (?:30|thirty) days|\u4e4b\u524d(?:30|\u4e09\u5341)\u5929",
    "week_before_last": r"week before last|two weeks ago|\u4e0a\u4e0a\u5468",
    "three_weeks_ago": r"three weeks ago|\u4e09\u5468\u524d",
}


def normalized_time_phrase(phrase: str) -> str:
    """Normalize surface wording only; never infer a missing date range."""
    phrase = phrase.strip().rstrip(".!?。！？").strip()
    return re.sub(r"^the\s+", "", phrase, flags=re.IGNORECASE)


def quoted_window(phrase: str) -> WindowProposal | None:
    """Parse a quoted supported phrase; grounding and date bounds are checked afterwards."""
    phrase = phrase.strip()
    normalized = normalized_time_phrase(phrase)
    for kind, pattern in ALIASES.items():
        if re.fullmatch(pattern, normalized.lower()):
            return WindowProposal(phrase=phrase, kind=kind)
    iso = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
    match = re.fullmatch(
        rf"(?:from\s+)?({iso})(?:\s*(?:to|through|until|\u81f3|\u5230)\s*({iso}))?",
        normalized,
        flags=re.IGNORECASE,
    )
    if match:
        try:
            start = date.fromisoformat(match[1])
            end = date.fromisoformat(match[2]) if match[2] else start
        except ValueError:
            return None
        return WindowProposal(phrase=phrase, kind="explicit", start_date=start, end_date=end)
    return None


def resolve_window(
    proposal: WindowProposal | None, users: list[str], s: TimeSemantics, max_days: int
) -> DateWindow | None:
    if proposal is None:
        return None
    phrase = proposal.phrase.strip().lower()
    # Ground in user text, never in the router's own suggested options.
    if not phrase or not any(
        re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", message.lower())
        for message in users
    ):
        return None
    if proposal.kind == "explicit":
        start, end = proposal.start_date, proposal.end_date
        if not start or not end or start.isoformat() not in phrase or end.isoformat() not in phrase:
            return None
    else:
        if not re.fullmatch(ALIASES[proposal.kind], normalized_time_phrase(phrase)):
            return None
        k = proposal.kind
        if k in ("today", "yesterday", "last_night"):
            start = end = s.today if k == "today" else s.yesterday
        elif k.startswith("past_"):
            end = s.today
            start = end - timedelta(days=int(k.split("_")[1]) - 1)
        elif k.startswith("previous_"):
            start, end = getattr(s, k + "_start"), getattr(s, k + "_end")
        elif k == "this_week":
            start, end = s.this_week_start, s.today
        else:
            start, end = getattr(s, k + "_start"), getattr(s, k + "_end")
    try:
        LocalDayWindow.from_dates(s.timezone, start, end, max_days)
    except ToolInputError:
        return None
    return DateWindow(start_date=start, end_date=end)


METRIC_ALIASES = {
    "steps": r"steps|step count|\u6b65\u6570",
    "heart_rate": r"heart rate|\u5fc3\u7387",
    "resting_heart_rate": r"resting heart rate|rhr|\u9759\u606f\u5fc3\u7387",
    "sleep_duration": r"sleep duration|sleep time|hours of sleep|\u7761\u7720\u65f6\u957f",
    "hrv_sdnn": r"sdnn",
    "hrv_rmssd": r"rmssd",
    "active_energy": r"active energy|active calories|\u6d3b\u52a8\u80fd\u91cf",
    "exercise_duration": r"exercise duration|exercise time|\u8fd0\u52a8\u65f6\u957f",
}


def grounded_metric(metric: Metric, users: list[str]) -> bool:
    pattern = (
        r"(?<![a-z0-9_])(?:" + metric.value + "|" + METRIC_ALIASES[metric.value] + r")(?![a-z0-9_])"
    )
    return any(re.search(pattern, message.lower()) for message in users)


ROUTER_PROMPT = """You are the intake router for HealthOps. Do not answer, query data, or diagnose.
Call route_request exactly ONCE. Return ALL SEVEN fields in every call:
decision, task, metrics, time_phrase, baseline_phrase, language, reason.

FIRST choose decision: ready, needs_clarification, or unsupported.
THEN choose task: summary, comparison, availability, reference, or unknown.
These are different fields. Never put unsupported in task. Never omit decision.

Supported: summaries/comparisons of the user's own recorded metrics, inventory of available
metrics, and reference definitions. Unsupported: diagnosis/medical advice, other people's data,
requests to invent values, or unrelated tasks. Unsupported uses task unknown and a refusal reason.
Missing scope is needs_clarification, NOT unsupported. Non-refusals always use reason none.
Broad wellbeing is a summary with unspecified metrics. Do not choose metrics on the user's behalf.
'How did I sleep' does not specify sleep duration or resting heart rate: ask for metrics.
Sleep duration and resting heart rate are distinct; include both only if both are requested.
Availability and reference tasks use metrics []. Reference does not need personal-data dates.

Quote time phrases exactly from USER messages. Python resolves dates. Do not calculate dates.
For missing time, recently, lately, usual, or other ambiguous phrases, use an EMPTY time phrase.
For comparison, time_phrase is current and baseline_phrase is earlier. Never swap them.
Empty strings and [] are intentional unknowns, not permission to invent defaults.
Merge replies with earlier USER-confirmed scope. Assistant suggestions are not confirmation.
A changed request replaces the old task/metrics; retain only scope that still applies.
Read all user messages to interpret short replies. Select en/zh from the user language.

Complete examples (illustrative, not facts about the current user):
User: Give my active energy on 2026-09-02.
route_request({"decision":"ready","task":"summary","metrics":["active_energy"],
"time_phrase":"2026-09-02","baseline_phrase":"","language":"en","reason":"none"})
User: List heart rate and exercise duration from 2026-09-02 to 2026-09-04.
route_request({"decision":"ready","task":"summary","metrics":["heart_rate","exercise_duration"],
"time_phrase":"2026-09-02 to 2026-09-04","baseline_phrase":"","language":"en","reason":"none"})
User: Give an overview of my wellbeing lately.
route_request({"decision":"needs_clarification","task":"summary","metrics":[],
"time_phrase":"","baseline_phrase":"","language":"en","reason":"none"})
User: Show my active energy lately. Assistant: Which period? User: This week.
route_request({"decision":"ready","task":"summary","metrics":["active_energy"],
"time_phrase":"This week","baseline_phrase":"","language":"en","reason":"none"})
User: Compare exercise duration this week against last week.
route_request({"decision":"ready","task":"comparison","metrics":["exercise_duration"],
"time_phrase":"this week","baseline_phrase":"last week","language":"en","reason":"none"})
User: Which measurements exist this week?
route_request({"decision":"ready","task":"availability","metrics":[],
"time_phrase":"this week","baseline_phrase":"","language":"en","reason":"none"})
User: Define RMSSD using the reference library.
route_request({"decision":"ready","task":"reference","metrics":[],
"time_phrase":"","baseline_phrase":"","language":"en","reason":"none"})
User: Tell me my roommate's exercise duration.
route_request({"decision":"unsupported","task":"unknown","metrics":[],
"time_phrase":"","baseline_phrase":"","language":"en","reason":"other_person"})
User: Should I change my medication?
route_request({"decision":"unsupported","task":"unknown","metrics":[],
"time_phrase":"","baseline_phrase":"","language":"en","reason":"medical_advice"})
"""


async def route(model: ChatModel, messages: list[dict], semantics: TimeSemantics):
    schema = router_tool_schema()
    turn = await model.complete([{"role": "system", "content": ROUTER_PROMPT}, *messages], [schema])
    if len(turn.tool_calls) != 1 or turn.tool_calls[0].name != "route_request":
        raise ModelError("Router did not return one structured decision.")
    try:
        p = RouterArguments.model_validate_json(turn.tool_calls[0].arguments_json).proposal()
    except ValueError as exc:
        raise ModelError("Router returned an invalid decision.") from exc
    users = [m["content"] for m in messages if m["role"] == "user"]
    window = resolve_window(p.window, users, semantics, 366 if p.intent == "availability" else 93)
    baseline = resolve_window(p.baseline, users, semantics, 93)
    confirmed_metrics = [metric for metric in p.metrics if grounded_metric(metric, users)]
    missing = []
    if p.intent == "unknown":
        missing.append("intent")
    if p.intent in ("summary", "comparison") and (
        not p.metrics or len(confirmed_metrics) != len(p.metrics)
    ):
        missing.append("metrics")
    if p.intent in ("summary", "comparison", "availability") and window is None:
        missing.append("time_range")
    if p.intent == "comparison":
        if baseline is None or (window and baseline.end_date >= window.start_date):
            missing.append("baseline")
    if p.route == "needs_clarification" and not missing:
        missing.append("intent")
    spec = RequestSpec(
        intent=p.intent,
        metrics=list(dict.fromkeys(confirmed_metrics)),
        window=window,
        baseline=baseline,
        language=p.language,
        unresolved_fields=missing,
    )
    decision = (
        "unsupported" if p.route == "unsupported" else "needs_clarification" if missing else "ready"
    )
    return decision, spec, p.reason


def clarification(spec: RequestSpec) -> str:
    # Unicode escapes keep public source English while providing localized UI strings.
    en = {
        "intent": "What recorded metric or reference topic would you like to explore?",
        "metrics": "Which metrics should I include: steps, sleep duration, or resting heart rate?",
        "time_range": "Which time range do you mean: past 7, 15 or 30 days, or explicit ISO dates?",
        "baseline": "What earlier, non-overlapping date range should I compare against?",
    }
    zh = {
        "intent": (
            "\u4f60\u60f3\u67e5\u770b\u54ea\u4e2a\u6307\u6807\u6216\u53c2"
            "\u8003\u77e5\u8bc6\u4e3b\u9898\uff1f"
        ),
        "metrics": (
            "\u4f60\u60f3\u770b\u54ea\u4e9b\u6307\u6807\uff1a\u6b65\u6570"
            "\u3001\u7761\u7720\u65f6\u957f\u6216\u9759\u606f\u5fc3\u7387"
            "\uff1f"
        ),
        "time_range": (
            "\u4f60\u6307\u6700\u8fd1 7\u300115 \u8fd8\u662f 30 \u5929\uff1f"
            "\u4e5f\u53ef\u4ee5\u63d0\u4f9b YYYY-MM-DD \u65e5\u671f\u8303"
            "\u56f4\u3002"
        ),
        "baseline": (
            "\u8bf7\u63d0\u4f9b\u66f4\u65e9\u4e14\u4e0d\u91cd\u53e0\u7684"
            "\u5bf9\u6bd4\u65e5\u671f\u8303\u56f4\u3002"
        ),
    }
    return " ".join((zh if spec.language == "zh" else en)[key] for key in spec.unresolved_fields)


def clarification_form(spec: RequestSpec) -> ClarificationForm:
    """UI hints from validated missing fields, never model-generated authorization."""
    fields = []
    for key in spec.unresolved_fields:
        field_spec = spec.model_copy(update={"unresolved_fields": [key]})
        options = []
        selection = "text"
        if key == "time_range":
            selection = "single"
            options = [
                ClarificationOption(
                    id=f"past_{days}_days",
                    label=f"Past {days} days"
                    if spec.language == "en"
                    else f"\u6700\u8fd1 {days} \u5929",
                    reply_text=f"past {days} days"
                    if spec.language == "en"
                    else f"\u6700\u8fd1{days}\u5929",
                )
                for days in (7, 15, 30)
            ]
        elif key == "metrics":
            selection = "multiple"
            options = [
                ClarificationOption(
                    id=m.value, label=m.value.replace("_", " ").title(), reply_text=m.value
                )
                for m in Metric
            ]
        fields.append(
            ClarificationField(
                id=key,
                label=clarification(field_spec),
                selection=selection,
                options=options,
            )
        )
    return ClarificationForm(fields=fields)
