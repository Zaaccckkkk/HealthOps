# Cross-device assistant interaction specification

Date: 2026-11-01. Target: the existing SwiftUI Ask tab. The companion browser prototype
is a synthetic interaction demonstration, not a second production client.

## Product tasks

1. Ask a precise question about personal recorded metrics and inspect its sources.
2. Turn a casual question into an explicit scope without guessing metrics or dates.
3. Compare source-specific records without silently merging or ranking devices.
4. Distinguish missing observations, unusable observations, service failures and empty reference retrieval.

Device discrepancy prediction is a future integration. Until a versioned result is available,
the interface must not identify a winning device or imply clinical accuracy.

## Conversation states and transitions

| State | Visible content | Actions and next state |
| --- | --- | --- |
| Empty | Purpose, data-source selector, two example questions | Type or choose an example -> submitting |
| Submitting | User message, indeterminate progress, stop control | Disable duplicate sends; response -> clarification/result/failure |
| Clarification | Only missing fields; date single-select, metric multi-select; free text | Confirm -> submitting; new question -> empty |
| Result | Server answer, scope and source labels, expandable evidence | New question; do not imply completed-answer conversational memory |
| No records | Explicit missing-data message, retained source/date scope | Change question or inspect available measurements |
| Failure | Distinct service/auth/conflict message | Edit and resubmit as a new question; never silently replay a consumed continuation |
| Interrupted | Explain that the server may already have processed the request | Start a fresh question; discard stale responses |

## Clarification card

Place clarification in the bottom composer area, separate from the answer transcript.
Use horizontal option rows and a compact free-text field; replace the ordinary composer
while clarification is pending. Long native option lists scroll within a bounded area.
Keep touch targets at least 44 points high.

For "How have I been doing recently?", show both missing fields together:

- Time range (single select): Past 7 days / Past 15 days / Past 30 days.
- Metrics (multiple select): Steps / Sleep duration / Resting heart rate; other supported metrics.
- Optional free text for explicit ISO dates, another metric, or a changed request.
- A visible confirmation summary before Send. No option is initially selected.

Selections are converted into ordinary user text. Free text is labelled as a correction and
must not be hidden or overwritten by selections. The router remains authoritative: a submitted
form is not proof that scope is valid. A partial response can trigger another clarification.
Old servers without a structured card continue to use text input.

## State and privacy

The server owns clarification transcripts, authenticated ownership, optimistic version checks,
and a fixed one-hour expiration from creation. The client owns display history and form drafts.
Persist only the pending clarification checkpoint in device-only Keychain storage, bound to
server, credential fingerprint, provider and timezone. Clear it before submitting a continuation
because a lost response may have consumed that version. Do not persist completed evidence or tokens
inside the checkpoint. Server checks remain mandatory even after local restoration.

Changing server, credentials, provider or account clears displayed history and continuation.
App relaunch can restore only a matching, unexpired pending checkpoint. Cancellation of a network
request is not proof of cancellation on the server. No automatic retries or fabricated progress steps.

## Visual and accessibility direction

Warm off-white canvas, black ink and outlined cards, monochrome selected controls, source chips,
and generous card spacing. Native dark mode uses inverse contrast. Error/warning accents remain
semantic rather than a green brand theme.
One primary action per card. Selection uses checkmarks and accessibility selected traits, not color
alone. Support Dynamic Type, VoiceOver labels, keyboard input, sufficient touch targets and vertical
scrolling. Evidence is secondary to the answer and expandable. Internal tool names and debug traces
are excluded from the user-facing flow.

## Acceptance and evaluation

- Partial, complete and revised scope; repeated ambiguous replies; cancellation and exhausted rounds.
- Multi-select plus custom text; no implied defaults; old-server text fallback.
- Relaunch recovery, expired checkpoints, different identity/scope, duplicate taps, late responses.
- Distinct missing/unusable/failed states; unsupported response never displayed as verified.
- Existing frozen diagnostic remains regression coverage. New interaction cases use scripted models
  for protocol assertions; report real-model accuracy only after a separate measured run.
- Training decisions classify failures as UX, state/protocol, retrieval/data, or model choice. Only
  repeated model-choice failures after upstream fixes qualify for reviewed training examples.

## Delivery sequence

1. This interaction specification.
2. A clickable synthetic prototype with clarification, results, failures and source comparison.
3. Structured backend clarification, native SwiftUI controls and scoped continuation recovery.
4. Automated interaction/protocol regressions plus a separate unmeasured generalization suite.
5. Evidence-backed error triage and post-training eligibility report; no speculative training run.
