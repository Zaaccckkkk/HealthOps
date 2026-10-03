"""Same-origin debug page for the assistant. A local verification interface, not a product UI.

The markup lives in debug_page.html next to this module. Everything coming from the API is
inserted with DOM text nodes, never as HTML. The bearer token is typed by the operator and kept
in sessionStorage only.
"""

from pathlib import Path

DEBUG_PAGE_HTML = (Path(__file__).with_name("debug_page.html")).read_text(encoding="utf-8")
