# Confirmed scope, coverage and one verification repair

This implementation applies when `RoutedAssistant` passes its server-created `RequestSpec`
to the execution assistant. It is not read from client JSON or inferred from model prose.
The direct core assistant remains usable without a spec for legacy execution-only tests;
its `completion_status` is `not_checked`. Existing baseline results do not measure these guards.

## Before execution

`assistant/guards.py::scope_error` rejects calls before `Toolbox.execute` if they do not match:

- the allowed tool family for the confirmed intent;
- one of the confirmed metrics;
- the exact confirmed date window, and baseline/current windows for comparisons;
- the request timezone and interface-selected provider, if a model tries to supply them.

A summary may use `daily_summary`, a comparison `period_comparison`; both can use `availability`
as a helper and `lookup_reference` for background. Availability does not satisfy metric coverage.
A reference-only request cannot read personal data. The availability helper returns a series
inventory for the same date window; it does not filter that inventory to the selected metrics.
Rejected calls consume the existing tool-call budget and return a structured error. They never
query the database. Their trace has `status: rejected` and `scope_guard_rejected`. A scoped
comparison is not silently reordered; wrong or swapped windows must be corrected by the model.
The underlying legacy toolbox still normalizes provider/timezone and reversed windows when
called directly without a request spec.

## Before accepting an answer

Python separately checks claim validity and response coverage:

- Every requested summary/comparison metric must have its corresponding tool result.
- Every returned provider/statistic card for that metric must be addressed.
- A daily card is covered by valid claims for all its rows, a valid whole-window missing-data
  statement, or `window_summary <evidence_id>`. One selected day cannot cover a longer window.
- `window_summary` renders a deterministic count of days with values, unusable records and no
  records, and points to the complete daily card for values, units, statistics and warnings.
  It does not compute a new health average or judge whether the values are healthy.
- `available_series <evidence_id>` describes the inventory card. Both new claim types take no date.
- Reference questions need at least one verified reference citation, not a quotation from every
  retrieved candidate. This does not validate topical relevance or completeness of a definition.

Responses add `coverage`, `completion_status` and `verification_attempts`. Coverage distinguishes
`not_queried`, `query_failed`, `no_records`, `unusable`, `insufficient_data` (comparison), and
`available`. `reported` indicates that valid findings address the required card scope. The
usual explanation can be `verified` only when both claims and coverage pass. Errors and refusals
retain their existing response behavior; cards remain available when an explanation fails.
These checks are structural, not a general semantic judge of user satisfaction or clinical quality.

## One correction opportunity

A scoped `state_findings` call must be standalone. The verify node can return to the same model
once, with invalid-claim reasons, missing targets, available evidence IDs and supported claim types.
The model must resubmit the complete findings, including previously valid items.

If all evidence was already fetched, only `state_findings` is allowed during repair. If a target
has not been queried, its intent's data tool is also exposed, and only still-unqueried targets can
be fetched. Scope guards remain active. Existing tool/model/context/deadline limits are not reset.
A second failed verification ends with the fixed fallback and `unverified`; there is no third
verification attempt. Tool failures are not retried as missing data. Free model prose is not
accepted as a corrected answer. No new LLM renderer or deployment was added.

## Synthetic regression examples

- A seven-day request with a thirty-day tool call: rejected before any query.
- Sleep plus resting HR, with no resting-HR records: a sleep-only finding fails coverage;
  resubmitting sleep plus a valid missing-data claim passes.
- A valid finding for one day of a two-day card: facts pass, coverage fails; `window_summary`
  can cover the full card without emitting every day's value in prose.
- Unknown `e1.healthkit` when the no-records card is actually `e1`: one corrected finding can
  pass without another database query. A second invalid ID ends unverified.
- Two returned sources: omitting either source fails coverage.
- Database failure: `query_failed`, not zero or `no_records`.

`tests/test_assistant_guards.py` uses scripted model calls and synthetic results to prove these
boundaries. `test_http_verified_repair_preserves_scope_and_empty_data` in the API tests uses
actual tools and the isolated PostgreSQL test DB, with a scripted invalid/corrected citation.
The execution prompt version changed; old frozen prompt hashes must not be relabeled as new
runs. Real-model routing, first-attempt accuracy, repair success and final completeness will
need a separate multi-turn evaluation. No real-model improvement is claimed by these tests.

## Observed verification

Final isolated Compose suite: 298 passed, 5 skipped (Node/Swift absent in that image).
Host-side policy, guard, DOM and Swift/Python contract selection: 49 passed, covering those
five skipped checks. Swift package: 53 passed. Ruff lint/format, local Markdown links and
public-content checks passed. No cloud deployment or actual-model accuracy run was performed.
