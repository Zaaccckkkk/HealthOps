"""Build reviewable synthetic writing references, never automatic gold labels.

Seventy prompts cover 35 scenario groups (50 development, 20 reserved).
Both paraphrases of a group share a split.
No model is called, no database is read, and no training file is exported.
"""

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from eval.build_answer_quality_additional import comparison
from eval.build_answer_quality_set import daily

ROOT = Path(__file__).parent / "writer-corpus"
VERSION = "writer-silver-v2"
RESERVED_V1_SHA256 = "58cbe48d6203019175049d63ec10116d0fe4e776aed76811896ce21ed27bf98b"
FAMILIES = (
    "partial_day",
    "sparse_constant",
    "nonmonotonic",
    "mixed_bases",
    "overlap",
    "missing",
    "tie",
    "apple_closer",
    "google_closer",
    "single_channel",
)
# Preserve the original three question pairs; append a new development pair per family.
QUESTIONS = {
    "partial_day": [
        "Did my activity crash today?",
        "Does today's low count prove I moved less?",
        "Am I much less active today?",
        "Can we call today an activity drop already?",
        "Should I read today's smaller number as a real activity decline?",
        "What can today's unfinished step recording actually tell me?",
        "Is the lower number so far today enough to judge my activity?",
        "Explain what is still uncertain about today's step count.",
    ],
    "sparse_constant": [
        "Were my steps the same every day?",
        "Can I call this a perfectly consistent week?",
        "Did I walk exactly the same amount throughout this period?",
        "Do repeated recorded values establish a constant daily pattern?",
        "Are the blank dates also days with this same count?",
        "How much consistency can you infer when some dates have no readings?",
        "Can matching observations tell us what happened on unrecorded dates?",
        "Should I assume the blank dates follow the same activity pattern?",
    ],
    "nonmonotonic": [
        "Was this a steady decline?",
        "Explain the pattern beyond just the first and last dates.",
        "Did my count drop on every recorded date?",
        "Was the downward change continuous?",
        "Does a lower final number mean the whole period trended steadily down?",
        "Describe the ups and downs, not just the endpoint difference.",
        "Was every change in this period downward?",
        "Summarize whether the counts fell smoothly or fluctuated.",
    ],
    "mixed_bases": [
        "Give me the daily average across these records.",
        "Can you average all of these values?",
        "What single mean represents these counts?",
        "Combine these dates into one average.",
        "Is averaging these differently defined readings meaningful?",
        "What prevents a comparable daily average here?",
        "Could these daily totals and interval sums share one meaningful mean?",
        "Explain why a single daily average may be unsupported.",
    ],
    "overlap": [
        "Why does one source have no usable total?",
        "Explain the unavailable source's warning.",
        "Are both data sources unusable?",
        "Which source has overlapping records?",
        "Does an overlap warning invalidate the other source too?",
        "Explain which source I can summarize without transferring the other warning.",
        "Is the unavailable total a source-specific problem?",
        "Describe the overlap issue without questioning the other source's values.",
    ],
    "missing": [
        "Do blank step records mean I was inactive?",
        "Was my activity zero on these dates?",
        "What do these missing records prove?",
        "Does no recording mean no walking?",
        "Can absence of readings tell you how much I moved?",
        "Should these blanks be filled with zero steps?",
        "Can we distinguish no movement from no measurement here?",
        "What conclusions should we avoid from the empty step history?",
    ],
    "tie": [
        "Which channel is nearer the model estimate?",
        "Is either reading closer to q50?",
        "Choose the reading with the smaller model distance.",
        "Which source is less distant?",
        "Do these equal offsets give either source an advantage?",
        "Can you identify a closer reading when distances match?",
        "Do the readings differ in closeness to the estimated center?",
        "Explain why neither reading wins this distance comparison.",
    ],
    "apple_closer": [
        "Which reading is closest, and why?",
        "Compare the sources' distances from the estimate.",
        "Which channel has the smaller absolute error against q50?",
        "Explain the nearest source without calling it ground truth.",
        "Which source is nearer this experimental estimate, not necessarily more accurate?",
        "State the closer source and the limit of that conclusion.",
        "Which source has the smaller offset and what does that imply?",
        "Tell me which reading is nearer while keeping the model uncertainty clear.",
    ],
    "google_closer": [
        "Which recorded source is nearer q50?",
        "Compare proximity to the experimental estimate.",
        "Does the smaller offset identify the true device?",
        "Which offset is smaller here?",
        "Who is closer to this model output, and does that establish accuracy?",
        "Explain the smaller distance without treating the model as truth.",
        "Which channel is nearer the estimate without proving device accuracy?",
        "Give the source with the smallest distance and explain the caveat.",
    ],
    "single_channel": [
        "Which device is more accurate with these readings?",
        "Can one usable channel establish the better device?",
        "Does the only available reading win the accuracy comparison?",
        "Should I trust this sole source over the missing one?",
        "Can we rank two devices when only one has a usable value?",
        "What prevents a device comparison in this window?",
        "Does missing data from the other device make this one better?",
        "Can the only available value settle the device-accuracy question?",
    ],
}


def scenario(family, variant):
    """Return cards, a draft reference and semantic requirements (not exact-match grading)."""
    base = 1800 + variant * 370
    if family == "partial_day":
        cards = [
            daily(
                "google_health",
                [base + 2200, base + 1400, base + 1900, base + 800, 240 + variant * 50],
            )
        ]
        answer = (
            "Today's recorded count is lower, but today's recording may still be "
            "incomplete. It does not establish an actual activity decline or "
            "support a direct comparison with complete days."
        )
        required = [
            "Distinguish lower recorded count from actual activity",
            "Explain incomplete today",
        ]
    elif family == "sparse_constant":
        cards = [daily("healthkit", [base, None, base, None, base])]
        answer = (
            f"The available recorded dates all show {base} steps, but some dates "
            f"have no readings. We cannot conclude that every day's activity was the "
            f"same or fill the gaps with that value."
        )
        required = [
            "Constant only on recorded dates",
            "Missing dates prevent an every-day conclusion",
        ]
    elif family == "nonmonotonic":
        cards = [daily("google_health", [base + 1000, base + 500, base + 2400, base + 1800, base])]
        answer = (
            "The recorded counts fluctuated rather than falling steadily: there was "
            "an increase within the period before later decreases. The last "
            "recorded count is below the first, but that endpoint difference alone "
            "does not describe the whole pattern."
        )
        required = [
            "Describe within-window increase and decreases",
            "Do not equate endpoint decline with steady decline",
        ]
    elif family == "mixed_bases":
        card = daily("healthkit", [base, base + 600, base + 300])
        card.rows[1].kind = "daily_total"
        card.statistic = "mixed daily_total and interval_total_sum; not comparable daily coverage"
        cards = [card]
        answer = (
            "I cannot give a comparable daily average from these cards: they mix a "
            "daily total with sums of recorded intervals. Those quantities do not "
            "establish matching daily coverage, so averaging them would be "
            "misleading."
        )
        required = ["Identify incompatible statistical bases", "Do not invent a combined mean"]
    elif family == "overlap":
        affected = "healthkit" if variant % 2 == 0 else "google_health"
        usable = "google_health" if affected == "healthkit" else "healthkit"
        cards = [
            daily(affected, [None] * 4, overlap=True),
            daily(usable, [base, base + 300, base + 100, base + 400]),
        ]
        answer = (
            f"The {affected} records overlap, preventing a reliable total for that "
            f"source. The {usable} recorded values remain usable for a summary; this "
            f"warning does not establish that either device is broken or more "
            f"accurate."
        )
        required = [f"Overlap belongs to {affected}", f"Preserve usable {usable} records"]
    elif family == "missing":
        cards = [daily("healthkit", [None] * 4)]
        answer = (
            "These cards contain no recorded step values for the period. That does "
            "not mean no walking occurred, and it does not justify filling the "
            "dates with zeros or inferring why the recordings are missing."
        )
        required = ["No recordings does not mean inactivity", "No zero filling or invented cause"]
    elif family in {"tie", "apple_closer", "google_closer"}:
        center = 5200 + variant * 430
        offsets = {"tie": (650, 650), "apple_closer": (150, 950), "google_closer": (1150, 250)}
        a, g = offsets[family]
        cards = [comparison(str(center - a), str(center + g), str(center))]
        if family == "tie":
            answer = (
                f"Neither channel is closer: both recorded values are {a} steps from "
                f"the model estimate. A tie against a synthetic-trained estimate "
                f"does not establish either device's real accuracy."
            )
        else:
            nearest = "healthkit" if a < g else "google_health"
            answer = (
                f"The {nearest} reading is closer to the model estimate: the "
                f"healthkit distance is {a} steps and the google_health distance is "
                f"{g} steps. This comparison does not establish which device is "
                f"truly more accurate."
            )
        required = [
            "Correct distances and tie/closest source",
            "Closeness does not establish accuracy",
        ]
    else:
        cards = [comparison(str(base + 1000), None, str(base + 1200))]
        answer = (
            "Only healthkit has a usable reading, so a device comparison is "
            "unavailable. Having the sole reading does not establish that Apple is "
            "more accurate than the source with no usable reading."
        )
        required = [
            "No comparison with a single usable channel",
            "No winner or experimental estimate as truth",
        ]
    return cards, answer, required


def records():
    output = []
    group_number = 0
    plans = [
        (family, variant)
        for index, family in enumerate(FAMILIES)
        for variant in ([0, 1, 2] if index < 5 else [0, 2])
    ]
    plans.extend((family, 3) for family in FAMILIES)
    for family, variant in plans:
        split = "reserved_test" if variant == 2 else "development"
        group = f"{family}-{variant}"
        cards, answer, required = scenario(family, variant)
        start = date(2025, 1, 1) + timedelta(days=group_number * 10)
        group_number += 1
        for card in cards:
            for i, row in enumerate(card.rows):
                row.date = str(start + timedelta(days=i))
            card.start_date = str(start)
            card.end_date = str(start + timedelta(days=max(0, len(card.rows) - 1)))
        end = max(date.fromisoformat(c.end_date) for c in cards)
        today = end if family == "partial_day" else end + timedelta(days=3)
        for paraphrase in range(2):
            output.append(
                {
                    "id": f"writer-v{2 if variant == 3 else 1}-{group}-{paraphrase}",
                    "version": VERSION if variant == 3 else "writer-silver-v1",
                    "scenario_group": group,
                    "synthetic_cohort": f"writer-fixture-{group_number:02d}",
                    "family": family,
                    "split": split,
                    "data_kind": "synthetic",
                    "language": "en",
                    "question": QUESTIONS[family][variant * 2 + paraphrase],
                    "today": str(today),
                    "cards": [c.model_dump(mode="json") for c in cards],
                    "reference_answer": answer,
                    "required_facts": required,
                    "forbidden_claims": [
                        "Unsupported health or causal inference",
                        "Invented values or sources",
                    ],
                    "manual_rubric": required
                    + [
                        "Direct answer in the requested language",
                        "Preserve source, date and unit scope",
                    ],
                    "label_provenance": "assistant_authored_silver",
                    "review_status": "pending_human_review",
                    "training_eligible": False,
                }
            )
    return output


def write():
    ROOT.mkdir(exist_ok=True)
    hashes = {}
    for split in ("development", "reserved_test"):
        rows = [r for r in records() if r["split"] == split]
        content = "".join(json.dumps(r, ensure_ascii=True, sort_keys=True) + "\n" for r in rows)
        name = f"{split}.synthetic.jsonl"
        if (
            split == "reserved_test"
            and hashlib.sha256(content.encode()).hexdigest() != RESERVED_V1_SHA256
        ):
            raise ValueError("Reserved v1 suite changed; do not silently rewrite the holdout")
        (ROOT / name).write_text(content)
        hashes[name] = hashlib.sha256(content.encode()).hexdigest()
    (ROOT / "manifest.json").write_text(
        json.dumps(
            {
                "version": VERSION,
                "new_prompts": 70,
                "scenario_groups": 35,
                "development_prompts": 50,
                "reserved_test_prompts": 20,
                "label_provenance": "assistant_authored_silver_not_gold",
                "independent_human_review": False,
                "model_evaluation_performed_at_creation": False,
                "limitations": [
                    "Shared author and scenario templates across splits",
                    "English writing only; not Router, tools, medical correctness "
                    "or multilingual coverage",
                ],
                "sha256": hashes,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    write()
    print("Wrote 50 development and 20 unchanged reserved prompts; all references await review.")
