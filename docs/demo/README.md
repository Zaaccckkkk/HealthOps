# Native iOS app demo

[Watch or download the 25-second video](app-demo.mp4)

Updated November 12, 2026 from the developer-supplied `demo_new_11_12.mp4`.
The main README displays a looping GIF preview in its original position; clicking the
preview or its caption opens the repository's MP4 file. Download the MP4 if the repository
file viewer does not offer inline playback.

## What the recording shows

1. Ask: `How did I do recently?`
2. Receive a request to clarify the metrics and time range.
3. Select `sleep_duration` and `active_energy`, then confirm the metric selection.
4. Specify `past 7 days` after the follow-up clarification.
5. Read the answer and expand the evidence section.

The data-source picker is **All channels**. The answer includes a warning that today's
recording may be incomplete. The recording demonstrates the visible application flow;
it is not an accuracy benchmark or a measurement of model response latency.

## Video and preview

| Asset | Export settings |
| --- | --- |
| Full video | MP4, H.264, 1080 x 2338, 30 fps, 24.97 seconds, approximately 2.1 MB |
| README preview | GIF, 360 x 779, 10 fps, looping, approximately 3.0 MB |
| README display | Centered, 300 pixels wide, linked to the full video |

The MP4 was compressed with x264 at CRF 24 using the slow preset, with fast-start metadata
for playback. The supplied AAC audio stream was copied without re-encoding; it is effectively
silent. The GIF has no audio. No additional cuts, speed changes, or replacement frames were
introduced; the preview reduces resolution, frame rate, and color depth.

## Recording context

The recording alone does not establish the account's data provenance, the model version,
the backend deployment, or whether the capture used a Simulator or a physical device.
The earlier November 2 demo's synthetic-account and runtime details do not describe this
replacement recording and are not carried forward as claims about it.

For setup instructions, see the [synthetic quickstart](../quickstart.md) and
[native client guide](../../src/frontend-app/HealthOpsUploader/README.md).
