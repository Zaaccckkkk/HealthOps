"""Additional invented stress cases, frozen before their first v2 model run.

Authored by the implementer: these are not an independently held-out benchmark.
"""

import json
from pathlib import Path

from eval.build_answer_quality_set import daily, reconcile


def comparison(apple, google, center):
    card = reconcile(True)
    card.reconciliation.update(
        providers=[
            {"provider": "google_health", "value": google},
            {"provider": "healthkit", "value": apple},
        ],
        q05=str(int(center) - 500),
        q50=center,
        q95=str(int(center) + 500),
    )
    if apple is None or google is None:
        card.warnings.append("single_device" if apple or google else "no_reading_on_date")
    return card


def cases():
    mixed = daily("google_health", [1800, 2200, 1600])
    mixed.rows[1].kind = "daily_total"
    definitions = [
        (
            "reversed_tie",
            "Which channel is nearer the estimate?",
            "en",
            [comparison("4100", "5900", "5000")],
            ["Tie: neither is closer", "Do not claim accuracy"],
        ),
        (
            "apple_closer",
            "Explain which reading is closer and why.",
            "en",
            [comparison("4700", "6400", "5000")],
            ["Apple distance 300, Google distance 1400", "Closeness is not accuracy"],
        ),
        (
            "google_closer",
            "Which reading is closer to the estimate?",
            "en",
            [comparison("3600", "5100", "5000")],
            ["Google distance 100, Apple distance 1400", "Closeness is not accuracy"],
        ),
        (
            "single_apple",
            "Can we now say Apple is the more accurate device?",
            "en",
            [comparison("4700", None, "5000")],
            ["Cannot compare with one reading", "No model estimate used as accuracy evidence"],
        ),
        (
            "neither_reading",
            "Which device should I believe today?",
            "en",
            [comparison(None, None, "5000")],
            ["No usable readings; cannot compare", "Do not invent a winner or estimate"],
        ),
        (
            "today_zh",
            "\u4eca\u5929\u8fd9\u4e48\u5c11\uff0c\u662f\u4e0d\u662f\u6d3b\u52a8\u91cf\u9aa4\u964d\u4e86\uff1f",
            "zh",
            [daily("google_health", [6300, 5700, 400])],
            [
                "Chinese explanation of incomplete today",
                "No definite conclusion about actual activity",
            ],
        ),
        (
            "historical_decrease",
            "Describe the change across these completed past dates.",
            "en",
            [daily("healthkit", [4300, 2700, 1900])],
            ["Recorded decline of 2400", "Do not call historical dates today or complete activity"],
        ),
        (
            "chinese_dates",
            "\u8bf7\u7528\u4e2d\u6587\u8bf4\u660e8\u67081\u65e5\u52308\u67083\u65e5\u7684\u6b65\u6570\u53d8\u5316\u3002",
            "zh",
            [daily("healthkit", [3600, 2900, 1400])],
            ["Supported dates and decrease of 2200", "No diagnosis or invented numbers"],
        ),
        (
            "english_dates",
            "Compare August 1 and August 3 using the recorded values.",
            "en",
            [daily("google_health", [1600, 2400, 3800])],
            ["Correct date/value attribution", "Increase of 2200 without invented statistics"],
        ),
        (
            "mixed_bases",
            "What is the daily average across these records?",
            "en",
            [mixed],
            ["Explain mixed bases are not comparable", "Do not compute an unsupported average"],
        ),
        (
            "sparse_constant",
            "Were my steps consistently the same every day?",
            "en",
            [daily("healthkit", [2600, None, 2600, None, 2600])],
            ["Only recorded days match", "Missing days prevent an every-day claim"],
        ),
        (
            "google_overlap",
            "Why is Google unavailable while Apple has values?",
            "en",
            [
                daily("google_health", [None] * 3, overlap=True),
                daily("healthkit", [1700, 2300, 2800]),
            ],
            ["Overlap belongs to Google only", "Keep Apple values usable"],
        ),
    ]
    return [
        {
            "id": name,
            "data_kind": "synthetic",
            "question": question,
            "language": language,
            "today": "2026-08-03" if name == "today_zh" else "2026-08-10",
            "cards": [c.model_dump(mode="json") for c in cards],
            "manual_rubric": rubric,
        }
        for name, question, language, cards, rubric in definitions
    ]


if __name__ == "__main__":
    path = Path(__file__).with_name("answer_quality.additional.synthetic.jsonl")
    path.write_text("".join(json.dumps(c, ensure_ascii=True) + "\n" for c in cases()))
    print(f"Wrote {len(cases())} additional synthetic cases")
