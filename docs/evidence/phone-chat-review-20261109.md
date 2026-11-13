# Chat presentation and Chinese sleep-unit review

## Change

The iOS Ask view presents questions and clarification replies as smaller right-aligned
bubbles. Assistant prose is left-aligned without a bubble. Clarification prompts wrap in
full, use subheadline text, and omit repeated status chips and disclaimers. Account details,
completed-answer data status and disclaimers remain expandable. Explanation provenance
and fallback notices remain visible; this UI does not upgrade generated text to verified text.

`ChatView.swift` owns the presentation. `AssistantConversation` and `AssistantModel` still
send the original user text and server-issued continuation, preserve source-labelled history
when switching channels, and invalidate history on identity changes. Completed history
remains in memory rather than durable storage.

## Verification

- `swift test`: 71 tests passed, including channel-switch history, stale-response rejection,
  clarification checkpoint scope and expiry, and response presentation contracts.
- Repository policy tests: 43 passed; public-content, whitespace and touched Markdown
  destination checks passed. Backend unit and Compose integration suites were not rerun.
- Xcode Debug builds succeeded for Simulator and the connected iPhone 15.
- The actual SwiftUI views were inspected in Simulator using a temporary copy with explicitly
  synthetic dialogue. The injected preview is not part of the installed physical-phone build.
- The final device build was installed on the previously confirmed iPhone 15. Manual
  acceptance of the new layout remains with the owner.
- No API behavior, writer prompt, database schema or Cloud Run configuration changed.

## Synthetic writing probe

Three development accounts from synthetic-v0.2 were exercised through the authenticated
in-process API, live Qwen3-8B router/executor/writer and real SQL tools in the isolated test
DB. Initial Chinese sleep questions requested clarification. After the metric was supplied,
all three reached the writer and passed the existing evidence audit. Two answers used raw
seconds; the account without a recorded value correctly described missing data.

One observed completion was retained as an unapproved Chinese preference candidate, with
its exact writer messages, evidence and rejected JSON. The proposed chosen answer expresses
each source's duration in hours/minutes, discloses rounding, and preserves the recorded-interval
coverage limitation. Python Decimal independently checks the conversion. The existing export
checker flags the converted numbers as `unsupported_number`; the candidate remains blocked
from training export pending conversion-aware validation and independent human review.

The frozen training, validation and test datasets were not changed. These development probes
are not a holdout score or proof of bilingual retention. One Chinese pair cannot establish
post-training Chinese/English quality. Personal-data diagnostics stay private and are never
used as examples, fixtures or training records.

## Follow-up: user-selected bubble colour and expanded Chinese probes

User bubbles now use sRGB `#A1CCA6` with black text for readable contrast in both themes.
No layout, protocol, prompt or server behaviour changed. Simulator and physical-device builds
succeeded; the colour was visually inspected using synthetic dialogue and installed on the
confirmed iPhone. No new tests were added for this cosmetic change; the preceding 71-test Swift
run remains the state/contract regression evidence, not a new test run.

A fresh development run covered nine Chinese sleep scenarios and fifteen turns across the same
three synthetic accounts: casual questions with clarification, explicit hours/minutes requests,
and seven-day questions resumed after clarification. All nine final responses invoked the writer
and passed the existing evidence-input audits. All nine were in Chinese. Of seven scenarios with
recorded durations, zero expressed those durations in hours/minutes: six used seconds and one
reported only directions. Two no-record scenarios correctly stated absence rather than zero.

Manual silver review found a range misrepresented as temporal change in one answer and an
endpoint change overstated as a trend across gaps in another. These are semantic limitations
that an evidence-input audit does not establish as correct. A separate Decimal conversion audit
records each synthetic source/day without replacing the original writer input. This is a
pretraining development baseline, not a held-out accuracy estimate. No candidates were approved
or added to the frozen train/validation/test splits, and the conversion export blocker remains.
