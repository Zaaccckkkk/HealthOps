# Reference trust boundary

Retrieved text and metadata are untrusted content. `reference_safety.py` screens known
instruction-override, role-spoofing, credential-request and executable-tool patterns after
Unicode normalization, including zero-width format characters. It also rejects unsafe URL
schemes and URL credentials. It is a conservative rule screen, not a complete injection
classifier; paraphrases, encoded attacks and benign instructional quotations remain limitations.

`lookup_reference_tool` screens full chunks before quotation truncation. The assistant screens
the result again before evidence is accumulated. Cards and claim verification provide additional
checks so a different retrieval adapter cannot bypass the rendering boundary. Filtering copies
the input and does not edit stored sources. Rejected passages are omitted, never rewritten.
If all passages are withheld, the answer explicitly reports that condition. Mixed results keep
only accepted passages and preserve a `reference_content_blocked` warning.

The model receives reference evidence IDs and supported claim types, not retrieved prose,
titles, URLs or echoed queries. Selection therefore relies on retrieval ranking rather than
model-based passage interpretation. Source text remains available to the server for quotations.
Normal quotation limits still apply. This trades semantic passage selection for a narrower model
input surface; retrieval relevance must be evaluated separately.

`explanation_status=verified` continues to mean that claims match their evidence. Reference cards
and the native UI explicitly distinguish source matching from safety or medical validity.
No claim is made that all accepted passages are safe. Corpus review, provenance controls,
live-model attack evaluations and normal-reference controls remain necessary.

Validation: 20 synthetic reference-safety cases cover attacks, benign controls, mixed results,
metadata, links, direct verifier bypass, full-chunk screening and an acknowledged rule bypass.
The isolated API suite passed 370 tests with 5 skips; Swift passed 60 tests and the simulator
app compiled. These checks do not measure a live model's injection success rate.

Reference: [OWASP prompt injection prevention](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html).

## Model connection failures

Model HTTP adapters use a separate five-second connection timeout and keep the existing
120-second response-read timeout. `HEALTHOPS_ASSISTANT_MODEL_CONNECT_TIMEOUT_SECONDS` can
configure the connection limit (positive, at most 30 seconds); the shorter of this limit and
the existing model timeout is used. The overall assistant request deadline still applies.
Read timeouts measure response inactivity, not an additional whole-request budget.

Connection refusal and connection timeout become `ModelConnectionError`; routing and execution
return a fixed model-service-unavailable message with the existing `model_error` status. They
are not retried automatically or misreported as an inability to understand the question.
Read/generation timeouts and invalid model output remain distinct model failures. This does not
guarantee detection of a server that accepts a connection but stalls during inference.

Tests inspect the HTTPX timeout extensions and simulate connection/read exceptions for both
adapters, assert one request per failed call, and check routing and execution responses. Mock
transports do not enforce wall-clock network timeouts; live-cloud measurements are separate.
See [HTTPX timeout semantics](https://www.python-httpx.org/advanced/timeouts/).
