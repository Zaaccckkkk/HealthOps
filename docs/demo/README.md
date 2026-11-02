# Native iOS Simulator demo

[Watch the 45-second video](simulator-demo.mp4)

![Recorded native app interaction](simulator-preview.gif)

Captured on 2026-11-02 UTC from the real SwiftUI application, using a dedicated synthetic
account and live Qwen3-8B inference. This is not the scripted HTML prototype and is not a
physical-iPhone recording. No application code or model responses were changed for the video.

## What the recording shows

1. Ask: `How have I been doing recently?`
2. Receive a server clarification form; select resting heart rate, sleep duration and past 7 days.
3. Submit `resting_heart_rate, sleep_duration; past 7 days` with the pending conversation ID/version.
4. Receive an answered response with `explanation verified against the cards`.
5. Inspect both evidence cards: seven heart-rate days, six sleep days, and one sleep day marked
   `no data`. Missing records are not displayed as zero.

The data-source picker is **All channels**. The retrieved records in these two cards belong to
synthetic `healthkit` observations; these labels do not establish that a wearable was read.
The answer is an evidence-based coverage summary, not a clinical assessment of wellbeing.

## Runtime and data boundary

- Native client: iPhone 17 Simulator, iOS 26.3, Debug build from application commit `3f2da45`.
- API and PostgreSQL: fresh local Docker Compose project `healthops-recording`, with its own volume.
- Model: local Ollama **0.35.0**, Apple M4 Pro / Metal, `qwen3:8b` Q4_K_M.
- Model digest: `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`.
- Account: `synthetic-simulator-demo`; 259 generated observations uploaded through the ingestion API.
- Data generator: `src/api-service/eval/synthetic_history.py`; its in-memory `TODAY` was set to
  **2026-11-01** before calling `observations()`. The checked-in generator and API clock were unchanged.
  This makes the demo's actual past-seven-day scope 2026-10-26 through 2026-11-01 in America/New_York.
- No personal database, real HealthKit history, cloud VM or OAuth connection was used.

The local demo is separate from the reported cloud evaluation (Ollama 0.35.1 and V100).
It adds one observed interaction, not a new accuracy or latency benchmark. RAG, real-device
HealthKit ingestion, OAuth sync, app restart recovery and network failure recovery are not
established by this recording.

## Editing disclosure

The MP4 is 45 seconds, 900 x 1920, without audio. Operation pauses were trimmed, one model-wait
segment is accelerated **6.3x** and labeled, and the final five seconds hold an actual screenshot
of the returned evidence cards, also labeled. Answers, values and UI elements were not replaced.
The GIF is a reduced-resolution 4 fps preview of the edited video.

The main original capture is approximately 81 seconds. Raw captures and local credentials stay
under the ignored `src/api-service/.local/simulator-demo/` directory and are not published.
Do not use edited timing to infer response latency.

## Reproduce and accept on a phone

Follow the [synthetic quickstart](../quickstart.md) and
[native client guide](../../src/frontend-app/HealthOpsUploader/README.md). The quickstart's
unmodified fixture dates end on 2026-10-02: use explicit matching dates or deliberately generate
a new synthetic cohort for the current date. Do not change the server clock to hide missing data.

For physical-iPhone acceptance, use a reachable API with a dedicated synthetic account.
`127.0.0.1` on an iPhone refers to the phone, not this Mac. Verify the endpoint and account before
asking; do not enable personal HealthKit upload for this synthetic acceptance.

Check: ambiguous question -> visible choices and free text -> confirmed scope -> verified answer
-> both metric cards and missing-data handling. Also test keyboard dismissal, scrolling,
clarification after app relaunch, and an intentional disconnected-network error. Record those
results separately; they remain pending for this build.
