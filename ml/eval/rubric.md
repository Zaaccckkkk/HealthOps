# Manual grading rubric for assistant answers

Grade each answer on the sheet the evaluation script writes. One row per question.

| Column | Values | Meaning |
|---|---|---|
| metric_correct | yes / no | The answer talks about the metric the question asked for (e.g. resting heart rate, not heart rate; SDNN never called RMSSD). |
| unit_correct | yes / no / n_a | Units match the tool (bpm, ms, count, kcal, s). |
| window_correct | yes / no | The dates named match the resolved semantics (last week = previous Monday to Sunday, past 7 days includes today). |
| provider_stated | yes / no / n_a | The data channel is named when more than one exists. |
| direction_correct | yes / no / n_a | Increase/decrease and the sign of the difference match the tool result. |
| caveats_kept | yes / partial / no | Sparse windows, overlapping intervals, interval-only totals, missing data are stated when the tool warned. |
| no_invention | yes / no | No numbers, dates or claims beyond the tool results; no medical advice. |
| verdict | correct / partially_correct / wrong / refused_correctly / refused_wrongly | Overall. |

Answers must use the tool's units and granularity: converting seconds to hours or computing an
average or total across days produces numbers the tools never returned, so such answers are
marked `unmatched` by the numeric diagnostic and `no_invention = no` here. Questions are written
to ask for per-day values or for a period comparison for that reason.

Numbers that match the tool output do not make an answer correct: "dropped from 70 to 60" with the
numbers swapped matches numerically and is wrong. Grade direction and window explicitly.
