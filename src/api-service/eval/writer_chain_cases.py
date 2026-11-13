"""Development-only full-chain questions and synthetic upload edge records.

No evidence cards, model quantiles or ideal answers are authored here. The ordinary
tools must produce them from persisted observations. Never use this as a holdout.
"""

from datetime import date, timedelta

from healthops.tools.local_days import LocalDayWindow

ACCOUNTS = {
    "synthetic-00000": ("2026-06-30", "2026-08-17"),
    "synthetic-00001": ("2026-05-14", "2026-08-07"),
    "synthetic-00003": ("2026-07-31", "2026-09-12"),
}
EDGE_ACCOUNT = "writer-chain-edges-v1"
ZONE = "America/New_York"


def edge_diagnostics():
    """Separate follow-up probes; never replace the original casual-question results."""
    rows = []
    for original in cases():
        if original["source_user_id"] != EDGE_ACCOUNT:
            continue
        args = original["expected_arguments"]
        row = dict(original)
        row["id"] += "-explicit-probe"
        row["questions"] = [
            f"Analyze my recorded steps from {args['start_date']} through {args['end_date']}. "
            "Summarize the pattern and recording limitations."
        ]
        row["diagnostic_of"] = original["id"]
        rows.append(row)
    return rows


def case(source, family, questions, tool, arguments, today, provider=None):
    return {
        "id": f"{source}-{family}",
        "source_user_id": source,
        "family": family,
        "split": "development",
        "data_kind": "synthetic",
        "timezone": ZONE,
        "today": str(today),
        "provider": provider,
        "questions": questions,
        "expected_tool": tool,
        "expected_arguments": arguments | {"timezone": ZONE},
    }


def cases():
    result = []
    for source, (first, target) in ACCOUNTS.items():
        first, target = date.fromisoformat(first), date.fromisoformat(target)
        start = target - timedelta(days=6)

        def summary(family, metric, begin, end, question, provider=None, today=None):
            result.append(
                case(
                    source,
                    family,
                    [question],
                    "daily_summary",
                    {
                        "metric": metric,
                        "start_date": str(begin),
                        "end_date": str(end),
                        "provider": provider,
                    },
                    today or target + timedelta(days=1),
                    provider,
                )
            )

        for metric in ("steps", "sleep_duration", "resting_heart_rate"):
            summary(
                metric + "_week",
                metric,
                start,
                target,
                f"Analyze my recorded {metric} from {start} through {target}. "
                "Explain the pattern and limitations, not just which dates have data.",
            )
        for provider in ("healthkit", "google_health"):
            summary(
                provider + "_steps",
                "steps",
                start,
                target,
                f"Summarize my recorded steps from {start} through {target}.",
                provider,
            )
        summary("yesterday", "steps", target, target, "How many steps were recorded yesterday?")
        summary(
            "today",
            "steps",
            target,
            target,
            "What do my recorded steps so far today tell me?",
            today=target,
        )
        result.append(
            case(
                source,
                "period",
                [
                    f"Compare my recorded steps from {start} through {target} with "
                    f"{start - timedelta(days=7)} through {start - timedelta(days=1)}."
                ],
                "period_comparison",
                {
                    "metric": "steps",
                    "provider": None,
                    "baseline_start": str(start - timedelta(days=7)),
                    "baseline_end": str(start - timedelta(days=1)),
                    "current_start": str(start),
                    "current_end": str(target),
                },
                target + timedelta(days=1),
            )
        )
        summary(
            "missing",
            "steps",
            first - timedelta(days=3),
            first - timedelta(days=1),
            f"Show my recorded steps from {first - timedelta(days=3)} through "
            f"{first - timedelta(days=1)}; do missing records mean I did not walk?",
        )
        for family, day in (
            ("reconcile", target),
            ("short_history", first + timedelta(days=2)),
            ("reconcile_missing", first - timedelta(days=1)),
        ):
            result.append(
                case(
                    source,
                    family,
                    [
                        f"Reconcile steps on {day}. Which channel is closer to the model estimate, "
                        "and what limits this comparison?"
                    ],
                    "reconcile_devices",
                    {"metric": "steps", "date": str(day)},
                    target + timedelta(days=1),
                )
            )
        result.append(
            case(
                source,
                "clarify_recent",
                ["How have I been doing recently?", "Recorded steps, the last 7 days."],
                "daily_summary",
                {
                    "metric": "steps",
                    "provider": None,
                    "start_date": str(start),
                    "end_date": str(target),
                },
                target,
            )
        )
        result.append(
            case(
                source,
                "clarify_device",
                [f"Which device is closer on {target}?", "Steps."],
                "reconcile_devices",
                {"metric": "steps", "date": str(target)},
                target + timedelta(days=1),
            )
        )
    for family, start, end, question, provider, today in (
        (
            "partial",
            "2025-03-01",
            "2025-03-05",
            "Did my activity crash today? Analyze recorded steps from March 1 to March 5, 2025.",
            None,
            "2025-03-05",
        ),
        (
            "gaps",
            "2025-03-10",
            "2025-03-14",
            "Were my steps the same every day from March 10 to March 14, 2025?",
            None,
            "2025-03-17",
        ),
        (
            "mixed",
            "2025-03-20",
            "2025-03-22",
            "Give me the daily average of my recorded steps from March 20 to March 22, 2025.",
            None,
            "2025-03-25",
        ),
        (
            "overlap",
            "2025-04-01",
            "2025-04-03",
            "Analyze steps from April 1 to April 3, 2025 across both sources, "
            "explaining any unusable totals.",
            None,
            "2025-04-06",
        ),
        (
            "overlap_reversed",
            "2025-04-10",
            "2025-04-12",
            "Explain which source has usable steps from April 10 to April 12, 2025 and why.",
            None,
            "2025-04-15",
        ),
        (
            "fluctuation",
            "2025-04-20",
            "2025-04-24",
            "Was the recorded step count steadily declining from April 20 to April 24, 2025?",
            None,
            "2025-04-27",
        ),
        (
            "missing",
            "2025-05-01",
            "2025-05-03",
            "Do missing step records from May 1 to May 3, 2025 mean I was inactive?",
            None,
            "2025-05-06",
        ),
        (
            "source_scope",
            "2025-04-01",
            "2025-04-03",
            "Summarize Google Health steps from April 1 to April 3, 2025.",
            "google_health",
            "2025-04-06",
        ),
    ):
        result.append(
            case(
                EDGE_ACCOUNT,
                family,
                [question],
                "daily_summary",
                {
                    "metric": "steps",
                    "provider": provider,
                    "start_date": start,
                    "end_date": end,
                },
                today,
                provider,
            )
        )
    return result


def edge_observations():
    rows = []

    def add(day, value, provider="healthkit", daily=False, hour=12, duration=1):
        day = date.fromisoformat(day)
        window = LocalDayWindow.from_dates(ZONE, day, day, 1)
        start, end = window.day_bounds(day)
        if not daily:
            start += timedelta(hours=hour)
            end = start + timedelta(hours=duration)
        rows.append(
            {
                "provider": provider,
                "source_record_id": f"writer-chain-edge-{len(rows)}",
                "metric": "steps",
                "unit": "count",
                "value": str(value),
                "started_at": start.isoformat(),
                "ended_at": end.isoformat(),
                "source_timezone": ZONE,
                "aggregation": "daily_total" if daily else "interval_total",
            }
        )

    for offset, value in enumerate([4000, 4400, 3500, 3000, 600]):
        add(str(date(2025, 3, 1) + timedelta(days=offset)), value, "google_health")
    for day in (10, 12, 14):
        add(f"2025-03-{day}", 2000)
    for day, value in ((20, 1800), (21, 2400), (22, 2100)):
        add(f"2025-03-{day}", value, daily=day == 21)
    for start, affected, usable in (
        (1, "healthkit", "google_health"),
        (10, "google_health", "healthkit"),
    ):
        for i in range(3):
            day = f"2025-04-{start + i:02}"
            add(day, 1000, affected, hour=12, duration=2)
            add(day, 1200, affected, hour=13, duration=2)
            add(day, 2100 + 100 * i, usable)
    for offset, value in enumerate([3000, 2500, 4400, 3800, 2000]):
        add(str(date(2025, 4, 20) + timedelta(days=offset)), value, "google_health")
    return rows
