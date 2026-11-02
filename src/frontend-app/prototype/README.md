# Assistant interaction prototype

Open `index.html` in a browser, or run `python3 -m http.server 8765 --directory
src/frontend-app/prototype` from the repository root and visit `http://localhost:8765`.

This self-contained, network-free prototype demonstrates the design in
[the interaction specification](../../../docs/design/assistant-experience.md). Use the casual
question to open time and metric choices, toggle multiple metrics, add custom text, confirm,
expand evidence and cancel. Developer-only state controls are hidden by default; open
`http://localhost:8765/?preview=1` to explore failure states. Narrow viewports use one column.
All numbers are illustrative synthetic examples, explicitly unrelated to the entered scope.
The scripted preview does not interpret arbitrary language, persist conversations, call an API,
or predict device discrepancies. Production behavior belongs to the native SwiftUI app.

Verified locally on 2026-11-01 in the browser: casual-question card, no default selection,
time selection, two metrics plus free text, confirmation summary, answer preview, expanded
source evidence and service-unavailable message. JavaScript syntax was checked with Node.
This is manual interaction evidence, not native iOS or real-model accuracy evidence.
