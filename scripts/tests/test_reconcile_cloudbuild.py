"""Artifact verification runs in explicit Python images, never an implicit SDK interpreter."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


def test_cloud_build_checks_artifacts_before_packaging(tmp_path):
    yaml = pytest.importorskip("yaml")
    root = Path(__file__).resolve().parents[2]
    config = yaml.safe_load(
        (root / "src/deployment/cloud-run/cloudbuild-reconcile.yaml").read_text()
    )
    steps = config["steps"]
    assert steps[0]["name"] == steps[2]["name"] == "python:3.12-slim"
    manifest = {
        "uri": "gs://synthetic-fixture/model/",
        "sha256": {"transformer.pt": hashlib.sha256(b"fixture weights").hexdigest()},
    }
    (tmp_path / "reconcile-model.json").write_text(json.dumps(manifest))
    subprocess.run([sys.executable, *steps[0]["args"]], cwd=tmp_path, check=True)
    assert (tmp_path / ".reconcile-source-uri").read_text() == manifest["uri"]
    folder = tmp_path / "models/reconcile"
    folder.mkdir(parents=True)
    (folder / "transformer.pt").write_bytes(b"fixture weights")
    subprocess.run([sys.executable, *steps[2]["args"]], cwd=tmp_path, check=True)
    assert json.loads((folder / "serving.json").read_text()) == manifest
    assert not (tmp_path / ".reconcile-source-uri").exists()
    (folder / "transformer.pt").write_bytes(b"tampered")
    rejected = subprocess.run(
        [sys.executable, *steps[2]["args"]],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode != 0
    assert "checksum mismatch" in rejected.stderr
