# Assistant UX validation

Date: 2026-11-01. Scope: structured clarification and native client integration, not cloud rollout.

## Observed checks

| Check | Result | What it establishes |
| --- | --- | --- |
| Browser prototype interaction | Passed manual walkthrough | Casual question, explicit time/multi-metric selection, free text, confirmation, evidence disclosure, failure state |
| SwiftUI simulator-target build | Succeeded, signing disabled | Actual App views/model compile with the core package |
| Swift core suite | 60 passed | Selection text, checkpoint expiry/scope, stale/duplicate responses, old-server fallback and existing ingestion contracts |
| Isolated Compose API suite | 347 passed, 5 host-only skips | Real PostgreSQL migrations, authentication, continuation and execution boundaries plus existing API regression suite |
| Host policy/iOS/eval/DOM suite | 46 passed | Host-only dependencies and existing cross-language/DOM checks; overlaps with container suite |
| New question set | 36 scenarios / 72 turns validated | Reproducible authorship, safe account allowlist, valid expected tool arguments; no model run |

Commands: `swift test` in the iOS package; `xcodebuild` for `HealthOpsUploaderApp` with generic
Simulator destination and `/tmp/HealthOpsUXDerivedData`; `docker compose --profile test build test`
and `docker compose --profile test run --rm test`; host pytest for `scripts/tests`,
`test_ios_contract.py`, `test_eval_sets.py`, `test_debug_page_dom.py`, `test_interaction_eval_set.py`.
Do not add these overlapping counts into a unique test total.

## Important test boundaries

- `test_assistant_interaction_http.py`: two synthetic test users, real authentication and isolated
  PostgreSQL. One user cannot resume the other's ID. A partial reply asks only the remaining field;
  replaying the old version returns 409 without another model call. Cancellation closes state.
- An expired database row returns 409 before invoking the model. A fresh app instance resumes a
  persisted question, executes the real daily-summary query for an empty synthetic account, and
  returns verified missing records with complete coverage, rather than a zero value.
- `test_clarification_form.py`: no default selections, fixed expiry, no premature tool execution,
  and empty reference retrieval exits without invented citations or futile model retries.
- `ClarificationTests.swift`: one time choice replaces another; metrics accumulate; custom correction
  remains visible. Wrong-scope/expired checkpoints cannot restore. A timeout clears continuation;
  a late cancelled or duplicate response cannot resurrect it. Unknown form versions use text input.
- Python generates `structured_clarification.json`; Swift decodes that exact API fixture. Volatile
  conversation ID/expiry are fixed synthetic values, not exported operational state.

One fixture initially reused the reserved synthetic-user UUID prefix, triggering the fixture
privacy check. Its conversation ID was changed to a distinct synthetic namespace; the check was
not weakened. The complete isolated suite was rerun successfully. Initial Xcode cache access
was sandbox-blocked; the build succeeded after granting cache access.

## Limits and release state

The model is scripted in protocol/integration tests. No new Qwen accuracy, usability study,
physical-device exercise, native UI automation or Keychain relaunch exercise is claimed. Native
Keychain wiring compiles; checkpoint serialization and restoration rules are tested in the core.
The browser prototype is deliberately scripted and separate from the production iOS client.
No new cloud resources were provisioned and no API traffic was switched. The previous live
63-scenario result applies to its recorded revision, not automatically to this UX implementation.
The three historical reference-routing passes still must not be called successful RAG answers.

Post-training triage was added after the integration run: its three targeted tests passed.
It was also executed on the existing v2/v3 real-model report directories. The v3 scan preserves
63 rubric passes while flagging the three empty-reference scenarios and creates no training
examples. This is reanalysis of historical outputs, not a new inference run.
