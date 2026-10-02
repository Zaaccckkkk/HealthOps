"""Check publishable working-tree files and staged blobs without printing their contents."""

import argparse
import re
import subprocess
from pathlib import Path, PurePosixPath

# Unicode escapes keep this policy implementation itself English-only.
HAN = re.compile("[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U000323af]")
LOCAL_LINK = re.compile(r"\]\([^)]*(?:docs/learning/|(?:\.\./)+learning/)[^)]*\)")


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout


def paths(raw: bytes) -> list[str]:
    return [name.decode("utf-8") for name in raw.split(b"\0") if name]


def forbidden_path(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        name.startswith("docs/learning/")
        or ".local" in path.parts
        or (path.name.startswith(".env") and path.name != ".env.example")
    )


def inspect_content(name: str, raw: bytes, origin: str) -> list[str]:
    issues = []
    label = f"{origin}: {name}"
    if forbidden_path(name):
        issues.append(f"{label}: local-only path must not be published")
    if HAN.search(name):
        issues.append(f"{origin}: filename contains Han characters")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return issues  # Binary assets need human review; this is a text policy check.
    for number, line in enumerate(content.splitlines(), 1):
        if HAN.search(line):
            issues.append(f"{label}:{number}: Han characters in publishable text")
        if name.endswith(".md") and LOCAL_LINK.search(line):
            issues.append(f"{label}:{number}: link to unpublished learning notes")
    return issues


def check_repository(root: Path, tracked_only: bool = False) -> list[str]:
    args = ["ls-files", "-z", "--cached"]
    if not tracked_only:
        args.extend(["--others", "--exclude-standard"])
    issues = []
    for name in sorted(set(paths(git(root, *args)))):
        file = root / name
        if file.is_symlink():
            issues.append(f"working tree: {name}: review symlink before publication")
        elif file.is_file():
            issues.extend(inspect_content(name, file.read_bytes(), "working tree"))
    # A cleaned working copy must not hide older disallowed text already staged for commit.
    staged = paths(git(root, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"))
    for name in staged:
        issues.extend(inspect_content(name, git(root, "show", f":{name}"), "staged"))
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracked", action="store_true", help="Skip untracked working-tree files")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    issues = check_repository(root, tracked_only=args.tracked)
    if issues:
        print("Public-content check failed (file contents are not printed):")
        print("\n".join(issues))
        return 1
    print("PASS: checked public text, local-only paths and staged content.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
