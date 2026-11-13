# Assistant guardrails

Implementation: `assistant-guardrails-v3-sensitive-output`, router policy `2026-11-12.2`.
English and Chinese remain the supported languages. This change adds no model, hosted safety
service, credentials, database migration or training data.

## Boundaries

1. Authentication and rate limiting precede routed execution. Conversation ownership/version
   checks still apply before accepting a continuation.
2. `RoutedAssistant.ask` screens the current and saved user messages before calling the router.
   A block returns `refused` / `routing_status=unsupported`; it neither calls the model/tools
   nor saves the offending turn. The pending conversation can resume with a clean reply.
   `Assistant.ask` also screens its question to protect direct/internal callers.
3. Structured router validation and deterministic tool scope checks remain mandatory. The
   server, not model arguments, supplies account identity. Screening does not grant permissions.
4. Retrieved references keep their existing screening/handle-only model interface. Source
   text is untrusted even when input screening passes.
5. The first writer draft is checked. A finding permits one editor call using the same model,
   unchanged evidence and existing budgets. The final candidate is formatted, supplied with
   server-owned qualifications, and screened for sensitive output. Credential requests/leaks
   and protected prompt disclosure preserve the verified template. Invalid JSON/schema and
   unexpected tool calls also fall back because they cannot produce the answer contract.
   Quality findings (unknown/missing IDs, dates/numbers and markup) are advisory feedback to
   the single Editor; they are not a second final veto. No third generation is attempted.

## Supported-language policy

`security.intake_issue` checks the current and saved user turns before any model call.
After NFKC normalization, letters must be Latin or Han (CJK ideographs). Other letters,
including Korean Hangul, Japanese kana, Cyrillic and Arabic, return `unsupported_language`.
This strict input-only rule applies to benign questions, attacks, mixed text, names and quoted
text alike. Digits, punctuation, emoji and English/Chinese mixtures remain allowed. Original
accepted text is forwarded unchanged. The direct assistant entry applies the same rule.
A language block returns a fixed request to rephrase in English or Chinese, with no model/tool
calls, no data cards and no verified-template fallback. Blocked continuations preserve state.

Script checking is not language identification. French/Spanish, romanized Korean and Han-only
Japanese can pass the script gate. The router prompt separately requires `unsupported` with
reason `unsupported_language` for requests/instructions in other languages, benign or malicious.
Python dispatches that decision to the same fixed refusal and never enters the tool/writer
path. This is a model policy, not a deterministic guarantee of recognizing every language.
Product names and short contextual replies must not alone trigger this model policy. The
scripted tests prove refusal dispatch, not real-model language identification accuracy.

## Screening and final checks

`security.input_issue` uses NFKC normalization and removes invisible format characters only
in its inspection copy. Patterns require an action plus a protected target; the mere presence
of a security term is insufficient. Explicitly negated actions and a narrow, whole-request
quoted-phrase explanation are permitted. Quoting arbitrary instructions grants no exemption.
The router prompt also treats user dialogue, role labels and encoded text as untrusted.

`security.output_issue` provides broad first-draft security feedback. The final gate uses
`security.sensitive_output_issue` for credential requests, credential-like assignments and
overlapping 80-character normalized excerpts from the known system prompts. This can catch substantial
verbatim disclosure; it does not detect all paraphrased or short disclosures. No credentials
are deliberately supplied to the model. Only version/stage/reason codes are logged by this
module, never prompt text, account identifiers, credentials or health data.

`narrative.finalize_draft` parses the response contract, screens sensitive content before
formatting, adds required qualifications, formats the prose and screens sensitive content
again. It does not run a second evidence/numeric/markup check. This is an explicit availability
tradeoff: an Editor may return an unsupported number, date or evidence ID. The first draft
checker and offline validation still report these quality errors, including ambiguous dates.
Input, account ownership, tool scope and structured findings verification are unchanged.

`explanation_status=model_generated` still does not mean semantic verification. Numerical
membership cannot prove which metric/date a value belongs to. `numeric_consistency` continues
to report `not_applicable` for generated prose because its legacy scanner targets templates;
its note describes the sensitive-output gate and the absence of edited-fact revalidation.
`narrative_reason` records a final failure; `narrative_check_reason` retains the first draft's
feedback even when the edited answer is returned.

## Verification and limits

Run from `src/api-service/`:

```sh
uv run pytest -q tests/test_assistant_security.py tests/test_assistant_narrative.py \
  tests/test_assistant_routing.py tests/test_assistant_guards.py tests/test_reference_safety.py
```

These are synthetic, scripted-model boundary tests. They assert that blocked input never calls
the router or tools, ordinary questions still reach routing unchanged, rejected continuations
preserve state, sensitive or malformed editor replies cannot replace templates, safe repairs
work, factual errors can intentionally pass the final gate, and model budgets do not grow. They are not GPU inference, live model red teaming, clinical validation,
or proof that every attack is blocked. The rule families and tests are developed together;
their pass rate is a regression result, not a held-out security estimate.

Known limits include paraphrases, shared-script/romanized language detection, encoding, ambiguity,
novel indirect injections and short/paraphrased prompt disclosures. Input filters and output
checks can both have false positives. A permitted request is still untrusted. Independent
adversarial testing against the actual serving model remains separate work. No DPO training,
cloud deployment, or live-model call is required for these deterministic boundaries.

Design reference: [OWASP prompt injection prevention](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html).
