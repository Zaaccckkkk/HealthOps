"""The knowledge manifest must be complete and the project's own documents must exist.

Third-party texts are not in the repository, so only the manifest and our own documents are
checked here; scripts/knowledge/fetch_sources.py --check verifies downloaded files locally.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/knowledge"))

from fetch_sources import load_manifest  # noqa: E402

REQUIRED = {
    "title",
    "publisher",
    "url",
    "file",
    "kind",
    "license",
    "license_url",
    "allowed_use",
    "retrieved_at",
    "sha256",
    "size_bytes",
    "topics",
}


def test_every_source_records_licence_and_checksum():
    sources = load_manifest()
    assert len(sources) >= 5
    for source in sources:
        missing = REQUIRED - set(source)
        assert not missing, (source["id"], missing)
        assert len(source["sha256"]) == 64
        assert source["url"].startswith("https://")
        assert source["allowed_use"], source["id"]


def test_source_ids_and_files_are_unique():
    sources = load_manifest()
    assert len({s["id"] for s in sources}) == len(sources)
    assert len({s["file"] for s in sources}) == len(sources)


def test_own_documents_exist_and_are_english_markdown():
    own = sorted((ROOT.parents[1] / "knowledge/own").glob("*.md"))
    assert {p.name for p in own} >= {
        "statistical-definitions.md",
        "warnings-glossary.md",
        "channels-and-hrv.md",
    }
    for path in own:
        text = path.read_text()
        assert text.startswith("# ") and "Source:" in text
