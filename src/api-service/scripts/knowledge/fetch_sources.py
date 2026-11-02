"""Download the third-party reference sources listed in knowledge/sources.yaml (repository root).

Files land in src/api-service/.local/knowledge/raw/ (ignored by Git and Docker) and are
verified against the sha256 recorded in the manifest, so everyone works from the same text that
was licence-checked.
Nothing is parsed or embedded here; this is collection only.
"""

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

# This file lives in src/api-service/scripts/knowledge/. The corpus description (manifest and
# the project's own notes) is at the repository root next to data/ and configs/; containers
# receive both locations through the environment (see docker-compose.yml, service rag-ingest).
SERVICE = Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = Path(
    os.environ.get("HEALTHOPS_KNOWLEDGE_DIR") or SERVICE.parent.parent / "knowledge"
)
MANIFEST = KNOWLEDGE_DIR / "sources.yaml"
RAW = Path(os.environ.get("HEALTHOPS_KNOWLEDGE_RAW_DIR") or SERVICE / ".local/knowledge/raw")
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.6 Safari/605.1.15"
)


def load_manifest() -> list[dict]:
    """Minimal reader for the flat `key: value` entries used in sources.yaml (no PyYAML)."""
    sources: list[dict] = []
    for line in MANIFEST.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("- id:"):
            sources.append({"id": stripped.split(":", 1)[1].strip()})
        elif (
            sources and ":" in stripped and not stripped.startswith("#") and line.startswith("    ")
        ):
            key, value = stripped.split(":", 1)
            value = value.strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            sources[-1][key.strip()] = value
    return sources


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="verify existing files, download nothing"
    )
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    failures = 0
    for source in load_manifest():
        target = RAW / source["file"]
        if not target.exists():
            if args.check:
                print(f"MISSING  {source['id']}")
                failures += 1
                continue
            request = urllib.request.Request(source["url"], headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=120) as response:
                target.write_bytes(response.read())
        digest = sha256(target)
        if digest != source["sha256"]:
            print(f"CHANGED  {source['id']}: sha256 {digest[:16]}... differs from the manifest")
            failures += 1
        else:
            print(f"ok       {source['id']}  ({target.stat().st_size} bytes, {source['license']})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
