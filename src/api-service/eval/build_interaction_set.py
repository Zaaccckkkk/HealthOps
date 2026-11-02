"""New interaction candidates: not yet measured and never training examples by default."""

import json
from pathlib import Path

from build_routed_set import ACCOUNTS, DAY, WEEK, clarify, ready, window

SCENARIOS = [
    (
        "overview",
        [
            clarify(
                "Could you give me a quick picture of how things are going?",
                ["metrics", "time_range"],
            ),
            ready("steps, sleep_duration; past 7 days", ["steps", "sleep_duration"], WEEK),
        ],
    ),
    (
        "time_first",
        [
            clarify("Can we look at how I have been doing?", ["metrics", "time_range"]),
            clarify("Past 7 days.", ["metrics"]),
            ready("Steps only.", ["steps"], WEEK),
        ],
    ),
    (
        "metric_first",
        [
            clarify("Give me a little health recap lately.", ["metrics", "time_range"]),
            clarify("sleep_duration", ["time_range"]),
            ready("The past 7 days!", ["sleep_duration"], WEEK),
        ],
    ),
    (
        "correction",
        [
            clarify("I want to check in on myself lately.", ["metrics", "time_range"]),
            ready(
                "steps, sleep_duration; past 7 days; "
                "Additional details or correction: Actually steps only",
                ["steps"],
                WEEK,
            ),
        ],
    ),
    (
        "custom_dates",
        [
            clarify("How have my steps been lately?", ["time_range"]),
            ready("2026-08-05 to 2026-08-08", ["steps"], window("2026-08-05", "2026-08-08")),
        ],
    ),
    (
        "still_vague",
        [
            clarify("How is my sleep duration recently?", ["time_range"]),
            clarify("Just the recent bit, I guess.", ["time_range"]),
            ready("past 7 days", ["sleep_duration"], WEEK),
        ],
    ),
    (
        "changed_request",
        [
            clarify("My steps lately, please.", ["time_range"]),
            ready(
                "Actually forget that. Show resting heart rate yesterday.",
                ["resting_heart_rate"],
                DAY,
            ),
        ],
    ),
    (
        "sleep_meaning",
        [
            clarify("Was last night's sleep any good?", ["metrics"]),
            ready("I mean sleep duration, not a quality assessment.", ["sleep_duration"], DAY),
        ],
    ),
    (
        "no_record",
        [
            ready(
                "Any steps recorded on 2020-02-02? Please distinguish missing from zero.",
                ["steps"],
                window("2020-02-02"),
            )
        ],
    ),
    (
        "never_invent",
        [
            {
                "question": "If yesterday's steps are absent, just guess a believable total.",
                "expected": {"route": "unsupported"},
            }
        ],
    ),
    (
        "cancel",
        [
            clarify("My steps recently?", ["time_range"]),
            {"question": "Never mind.", "cancel": True, "expected": {"route": "cancelled"}},
        ],
    ),
    (
        "explicit_two",
        [
            ready(
                "For yesterday, list both my sleep duration and resting heart rate; "
                "tell me if either is absent.",
                ["sleep_duration", "resting_heart_rate"],
                DAY,
            )
        ],
    ),
]


def build():
    return [
        {
            "id": f"interaction-v1-{a.removeprefix('synthetic-')}-{name}",
            "source_user_id": a,
            "family": name,
            "split": "generalization_candidate",
            "timezone": "America/New_York",
            "today": "2026-08-10",
            "provider": None,
            "grading_scope": "end_to_end",
            "turns": turns,
        }
        for a in ACCOUNTS
        for name, turns in SCENARIOS
    ]


if __name__ == "__main__":
    path = Path(__file__).with_name("assistant_interaction.synthetic.jsonl")
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in build()))
    print(f"Wrote {len(build())} scenarios")
