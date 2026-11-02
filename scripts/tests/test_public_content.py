import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "check_public_content.py"
spec = importlib.util.spec_from_file_location("public_content", SCRIPT)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


@pytest.fixture
def repository(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def stage(root, *names):
    subprocess.run(["git", "-C", str(root), "add", *names], check=True)


def test_english_passes(repository):
    (repository / "README.md").write_text("# Public English documentation\n")
    assert policy.check_repository(repository) == []


def test_untracked_han_fails(repository):
    (repository / "README.md").write_text("\u4e2d\u6587\n")
    assert any("Han characters" in issue for issue in policy.check_repository(repository))
    assert policy.check_repository(repository, tracked_only=True) == []


def test_local_notes_are_ignored(repository):
    (repository / ".gitignore").write_text("/docs/learning/\n")
    notes = repository / "docs/learning"
    notes.mkdir(parents=True)
    (notes / "note.md").write_text("\u5b66\u4e60\n")
    assert policy.check_repository(repository) == []


def test_force_added_notes_fail_even_if_english(repository):
    notes = repository / "docs/learning"
    notes.mkdir(parents=True)
    (notes / "note.md").write_text("Private learning notes\n")
    stage(repository, "docs/learning/note.md")
    assert any("local-only path" in issue for issue in policy.check_repository(repository))


def test_staged_text_checked_even_if_working_copy_is_clean(repository):
    readme = repository / "README.md"
    readme.write_text("\u4e2d\u6587\n")
    stage(repository, "README.md")
    readme.write_text("English replacement, not staged\n")
    assert any(issue.startswith("staged:") for issue in policy.check_repository(repository))


def test_public_link_to_private_notes_fails(repository):
    (repository / "README.md").write_text("[Local notes](docs/learning/note.md)\n")
    assert any("unpublished" in issue for issue in policy.check_repository(repository))


def test_private_credentials_path_fails_without_printing_content(repository):
    (repository / ".env").write_text("PLACEHOLDER_SECRET=do-not-print-this\n")
    issues = policy.check_repository(repository)
    assert any("local-only path" in issue for issue in issues)
    assert all("do-not-print-this" not in issue for issue in issues)


def test_filename_and_binary_policy():
    assert policy.inspect_content("assets/logo.png", b"\x89PNG\xff", "working tree") == []
    assert policy.inspect_content("\u4e2d.md", b"English text", "working tree")
