"""Development probes using existing synthetic-v0.2 cohorts, never authored cards.

Each query is a required scope check against the ordinary SQL tool. These are
not gold natural-language answers or an independent numerical oracle.
"""

from datetime import date, timedelta

from eval.writer_chain_cases import ACCOUNTS, ZONE


def cases():
    rows = []

    def add(source, family, metrics, questions, provider=None, today=None):
        end = date.fromisoformat(ACCOUNTS[source][1])
        start = end - timedelta(days=6)
        queries = [
            {
                "tool": "daily_summary",
                "arguments": {
                    "metric": metric,
                    "start_date": str(start),
                    "end_date": str(end),
                    "provider": provider,
                    "timezone": ZONE,
                },
            }
            for metric in metrics
        ]
        rows.append(
            {
                "id": f"{source}-multi-{family}",
                "source_user_id": source,
                "family": family,
                "split": "development",
                "data_kind": "synthetic",
                "timezone": ZONE,
                "today": str(today or end + timedelta(days=1)),
                "provider": provider,
                "questions": [q.format(start=start, end=end) for q in questions],
                "expected_queries": queries,
            }
        )

    for source in ACCOUNTS:
        add(
            source,
            "rhr_sleep",
            ["resting_heart_rate", "sleep_duration"],
            [
                "Summarize my recorded resting heart rate and sleep duration from {start} "
                "through {end} across all available sources. Explain both patterns and "
                "limitations, without making a medical judgment."
            ],
        )
        add(
            source,
            "rhr_both_hrv",
            ["resting_heart_rate", "hrv_sdnn", "hrv_rmssd"],
            [
                "Analyze my recorded resting heart rate, HRV SDNN, and HRV RMSSD from "
                "{start} through {end}. Keep the HRV definitions and sources separate; "
                "say if any requested metric is missing."
            ],
        )
        add(
            source,
            "casual_resume",
            ["resting_heart_rate", "hrv_rmssd"],
            [
                "How have I been doing recently?",
                "Show the recorded patterns in resting heart rate and HRV RMSSD over "
                "the last 7 days, across all sources. I am not asking for a diagnosis.",
            ],
            today=date.fromisoformat(ACCOUNTS[source][1]),
        )
        add(
            source,
            "heart_vs_resting",
            ["heart_rate", "resting_heart_rate"],
            [
                "Compare the recorded patterns for heart rate and resting heart rate "
                "from {start} through {end}. Keep these two metrics distinct."
            ],
        )

    source = "synthetic-00001"
    for provider, hrv in (("healthkit", "hrv_sdnn"), ("google_health", "hrv_rmssd")):
        add(
            source,
            provider,
            ["resting_heart_rate", hrv],
            [
                "Summarize resting heart rate and " + hrv + " from {start} through {end}, "
                "using only my selected source. Explain the recorded patterns."
            ],
            provider=provider,
        )
    add(
        source,
        "generic_hrv",
        ["resting_heart_rate", "hrv_sdnn", "hrv_rmssd"],
        [
            "How did my recorded resting heart rate and heart rate variability look "
            "from {start} through {end}? Use all available channels."
        ],
    )
    add(
        source,
        "wellbeing_boundary",
        ["resting_heart_rate", "hrv_rmssd"],
        [
            "I may not be doing very well. Analyze my resting heart rate and HRV RMSSD "
            "from {start} through {end}. What do the recordings show?"
        ],
    )
    add(
        source,
        "three_metrics",
        ["resting_heart_rate", "sleep_duration", "hrv_rmssd"],
        [
            "Describe my recorded resting heart rate, sleep duration and HRV RMSSD "
            "from {start} through {end}. Cover each metric and source without "
            "assuming that one caused another."
        ],
    )
    add(
        source,
        "explicit_resume",
        ["resting_heart_rate", "hrv_sdnn", "hrv_rmssd"],
        [
            "How have I been doing recently?",
            "For {start} through {end}, summarize resting heart rate plus both "
            "HRV SDNN and HRV RMSSD separately. Describe the recordings, not my health status.",
        ],
    )
    return rows
