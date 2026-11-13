# Writer preference review merge — November 8, 2026

Two locally annotated workbooks were merged by scenario ID. The output contains 23 cases with
saved human feedback (16 earlier cases and seven multi-metric cases), plus 13 other replay
candidates without saved feedback. Original workbooks remain unchanged. Review files, raw
captures and draft pairs remain in ignored local storage; no health records or workbook
contents are published here.

## Feedback and provenance

Revisions favor direct answers, plain-language explanations of overlapping intervals and
partial-day coverage, and a supportive opening when the user expresses concern. Factual
questions keep a direct tone. Source attribution, missingness and uncertainty remain intact.

The first 11 earlier reviews referred to historical answers. Their feedback was applied to the
latest captured input rather than pairing an old answer with new evidence. In particular,
device-to-estimate distances use current replay evidence. These revisions require renewed
confirmation. The workbook retains prior decisions and reasons alongside a separate final
decision column; it does not infer approval of a changed answer.

All 36 candidate prompts, evidence packets and rejected completions were checked against
existing replay results or captured writer calls. No new inference or training was performed.
The exact model inputs are preserved separately from readable evidence displays.

## Five input-contract blockers

Five proposed answers follow the reviewer's request to give an arithmetic mean of recorded
values, then distinguish it from a full-day average. However,
`src/api-service/healthops/assistant/narrative.py` instructs the writer not to calculate new
statistics, and these captured mixed-basis packets do not provide the requested mean.

These pairs remain blocked even where the numeric validator passes: a computed mean can
coincidentally equal an existing value without being an authorized statistic. One revised
answer explicitly triggers `unsupported_number`; the other four still conflict with the
prompt contract. Supply the statistic through the tool/evidence contract, or deliberately
revise that contract, then replay and review before making these pairs training eligible.
The merge changes neither the application behavior nor captured prompts to hide this conflict.

## Dataset readiness

- **Training:** 36 development candidates, including 23 with applied human feedback. None is
  marked training eligible pending final review and eligibility checks.
- **Validation:** no separately frozen writer-training validation set yet.
- **Independent test:** the former 20 reserved questions were used for candidate mining in the
  79-question replay. They are development material now, not an independent final test.
- **Next:** capture separate synthetic account/history groups through the actual chain, group
  shared evidence and paraphrases within one split, and freeze validation/test before training.
  There are 29 distinct exact packets among the 36 candidates; that count alone does not detect
  all overlapping histories or establish 29 independent examples.

Running a frozen baseline is not itself test leakage. Using test failures to choose training
examples or change prompts/labels makes the set part of development. Base and trained writers
should still be compared on the same newly frozen test after model selection on validation.
Existing application regression suites remain useful but do not replace this writer holdout.

## Verification and limits

- Verified all 3,832 exported table cells and reconstructed 144 exact input/output strings
  from workbook chunks, checking their SHA-256 digests.
- Preserved 52 source review rows, including all 23 cases with feedback, and checked that both
  annotated source workbooks retained their original byte hashes.
- Rendered and visually inspected all five output tabs; scanned for spreadsheet error cells.
- The existing basic writer validator accepted 35 revised candidate completions and flagged
  the expected unsupported mean in one. This is not a semantic accuracy score; five contract
  blockers remain and all revised preferences still await human confirmation.
- No production code, datasets, cloud deployment or model weights changed. Runtime integration,
  Swift, cloud inference and training tests were not run for this artifact/documentation task.

After review, eligible pairs can seed a small preference-training experiment. Their count and
approval do not establish that DPO or SFT will improve held-out performance.

## Follow-up: applying saved final revisions

The returned workbook contains 29 wording approvals and seven requested replacement answers.
The seven replacements were checked against captured evidence and applied verbatim to the
chosen-answer column; the other 29 answers remain unchanged. Saved decisions and comments
remain intact, with a separate status explaining which replacements have been applied.
The revised workbook's exact-output chunks and local draft JSONL now match all 36 current
chosen answers. Replacement application is not recorded as a new human approval.

The follow-up verified 4,044 exported cells, reconstructed 144 exact strings, and checked
preservation of table ranges, row/column sizing, panes, validations, conditional rules and
formulas. The incoming workbook is unchanged. All seven replacement completions pass the
existing basic writer validator. The five arithmetic-contract holds remain unresolved, and
all draft training-eligibility flags remain false. No model inference, application change,
training run or independent validation/test creation occurred in this follow-up.
