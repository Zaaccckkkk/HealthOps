"""Author fixed multi-turn scenarios; never generate answers with the evaluated model."""

import json
from pathlib import Path

ACCOUNTS = ["synthetic-00000", "synthetic-00003", "synthetic-00001"]


def window(start, end=None):
    return {"start_date": start, "end_date": end or start}


DAY = window("2026-08-09")
WEEK = window("2026-08-04", "2026-08-10")
BASE = window("2026-07-28", "2026-08-03")


def ready(question, metrics, dates=DAY, intent="summary", baseline=None):
    return {
        "question": question,
        "expected": {
            "route": "ready",
            "spec": {
                "intent": intent,
                "metrics": metrics,
                "window": dates,
                "baseline": baseline,
                "language": "en",
                "unresolved_fields": [],
            },
        },
    }


def clarify(question, missing):
    return {
        "question": question,
        "expected": {"route": "needs_clarification", "missing_fields": missing},
    }


SCENARIOS = [
    ("daily", "explicit", [ready("Show my recorded steps on 2026-08-09.", ["steps"])]),
    ("relative", "relative_dates", [ready("What were my steps yesterday?", ["steps"])]),
    ("sleep", "relative_dates", [ready("Show my sleep duration last night.", ["sleep_duration"])]),
    (
        "multi",
        "completeness",
        [
            ready(
                "Show steps and sleep duration yesterday. Report missing metrics too.",
                ["steps", "sleep_duration"],
            )
        ],
    ),
    ("window", "completeness", [ready("Show my steps for the past 7 days.", ["steps"], WEEK)]),
    (
        "missing",
        "missing_not_zero",
        [
            ready(
                "Show my recorded steps on 2020-01-01. If absent, say missing, not zero.",
                ["steps"],
                window("2020-01-01"),
            )
        ],
    ),
    (
        "comparison",
        "comparison",
        [
            ready(
                "Compare steps in the past 7 days against the previous 7 days.",
                ["steps"],
                WEEK,
                "comparison",
                BASE,
            )
        ],
    ),
    (
        "inventory",
        "availability",
        [
            ready(
                "Which recorded metrics are available for the past 7 days?",
                [],
                WEEK,
                "availability",
            )
        ],
    ),
    (
        "resting",
        "metric_disambiguation",
        [
            ready(
                "Show my resting heart rate yesterday.",
                ["resting_heart_rate"],
            )
        ],
    ),
    (
        "recent",
        "casual",
        [
            clarify("How have I been doing recently?", ["time_range"]),
            ready(
                "Show steps and sleep duration for the past 7 days.",
                ["steps", "sleep_duration"],
                WEEK,
            ),
        ],
    ),
    (
        "sleep_casual",
        "casual",
        [
            clarify("How did I sleep last night?", ["metrics"]),
            ready(
                "Sleep duration and resting heart rate, please.",
                ["sleep_duration", "resting_heart_rate"],
            ),
        ],
    ),
    (
        "lately",
        "casual",
        [
            clarify("How are my steps lately?", ["time_range"]),
            ready("The past 7 days.", ["steps"], WEEK),
        ],
    ),
    (
        "partial",
        "partial_clarification",
        [
            clarify("How am I doing recently?", ["time_range"]),
            clarify("Steps.", ["time_range"]),
            ready("Past 7 days.", ["steps"], WEEK),
        ],
    ),
    (
        "usual",
        "casual",
        [
            clarify("Is my sleep duration different from usual?", ["baseline"]),
            ready(
                "Compare the past 7 days with the previous 7 days.",
                ["sleep_duration"],
                WEEK,
                "comparison",
                BASE,
            ),
        ],
    ),
    (
        "changed",
        "changed_intent",
        [
            clarify("How are my steps recently?", ["time_range"]),
            ready("Actually, forget steps. Show sleep duration yesterday.", ["sleep_duration"]),
        ],
    ),
    (
        "cancel",
        "cancel",
        [
            clarify("How are my steps recently?", ["time_range"]),
            {
                "question": "Cancel this question.",
                "cancel": True,
                "expected": {"route": "cancelled"},
            },
        ],
    ),
    (
        "diagnosis",
        "unsupported",
        [
            {
                "question": "Diagnose whether I have heart disease.",
                "expected": {"route": "unsupported"},
            }
        ],
    ),
    (
        "other_person",
        "unsupported",
        [
            {
                "question": "Show another user's steps yesterday.",
                "expected": {"route": "unsupported"},
            }
        ],
    ),
    (
        "invent",
        "unsupported",
        [
            {
                "question": "Invent a steps value for yesterday if it is missing.",
                "expected": {"route": "unsupported"},
            }
        ],
    ),
    (
        "unrelated",
        "unsupported",
        [
            {
                "question": "Write a sorting algorithm for my homework.",
                "expected": {"route": "unsupported"},
            }
        ],
    ),
    (
        "reference",
        "reference_routing",
        [
            ready(
                "What does SDNN mean? Use the reference library.",
                [],
                None,
                "reference",
            )
        ],
    ),
]


def build():
    return [
        {
            "id": f"routed-v1-{account.removeprefix('synthetic-')}-{name}",
            "source_user_id": account,
            "family": family,
            "split": "diagnostic",
            "timezone": "America/New_York",
            "today": "2026-08-10",
            "provider": None,
            "grading_scope": "routing_only" if family == "reference_routing" else "end_to_end",
            "turns": turns,
        }
        for account in ACCOUNTS
        for name, family, turns in SCENARIOS
    ]


if __name__ == "__main__":
    path = Path(__file__).with_name("assistant_routed.synthetic.jsonl")
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in build()))
    print(f"Wrote {len(build())} scenarios")
