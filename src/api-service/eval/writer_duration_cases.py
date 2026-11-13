"""Supplemental unit-conversion tests on accounts excluded from preference training.

The original twenty-scenario holdout remains unchanged. Freeze these six cases and
capture their evidence through the authenticated API before any model training.
"""

from datetime import date, timedelta

from eval.writer_chain_cases import ZONE

ACCOUNTS = {
    "synthetic-00004": ("2026-06-10", "2026-09-08"),
    "synthetic-00013": ("2026-07-12", "2026-09-26"),
    "synthetic-00014": ("2026-06-22", "2026-09-13"),
}


def cases():
    rows = []
    for user, (_, last) in ACCOUNTS.items():
        end = date.fromisoformat(last)
        start = end - timedelta(days=6)
        for language in ("zh", "en"):
            question = (
                f"\u8bf7\u7528\u5c0f\u65f6\u548c\u5206\u949f\u8bf4\u660e{start}\u81f3{end}\u5404\u6765\u6e90\u6709\u8bb0\u5f55\u7684\u7761\u7720\u65f6\u957f\u8303\u56f4\uff0c\u5e76\u89e3\u91ca\u7f3a\u5931\u8bb0\u5f55\u7684\u9650\u5236\u3002"
                if language == "zh"
                else f"Describe the range of recorded sleep durations for each source from {start} "
                f"through {end} in hours and minutes, including limitations from missing records."
            )
            rows.append(
                {
                    "id": f"writer-duration-holdout-v1-{user}-{language}",
                    "source_user_id": user,
                    "scenario_group": user,
                    "family": "sleep_duration_units",
                    "split": "heldout_test",
                    "data_kind": "synthetic",
                    "timezone": ZONE,
                    "today": str(end + timedelta(days=1)),
                    "provider": None,
                    "questions": [question],
                    "expected_queries": [
                        {
                            "tool": "daily_summary",
                            "arguments": {
                                "metric": "sleep_duration",
                                "provider": None,
                                "timezone": ZONE,
                                "start_date": str(start),
                                "end_date": str(end),
                            },
                        }
                    ],
                    "manual_rubric": [
                        "Use the question's language and hours/minutes for recorded durations.",
                        "Validate whole durations against source-specific SQL values; "
                        "disclose rounding.",
                        "Range endpoints are minimum/maximum, not a claim of growth over time.",
                        "Preserve missingness, source attribution and "
                        "recorded-interval coverage limits.",
                    ],
                    "training_eligible": False,
                }
            )
    return rows
