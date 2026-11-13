"""Cloud contract tests use in-memory objects; no GCP calls or health records."""

import hashlib
import json
from pathlib import Path

import pytest

from writer_dpo.cloud import (
    claim_output,
    fetch_manifest,
    finalize_results,
    gcs_path,
    receipt,
    sync_results,
    upload_results,
)
from writer_dpo.snapshot import upload_snapshot
from writer_dpo.vertex import job_spec


class MemoryStore:
    def __init__(self, objects):
        self.objects = objects
        self.writes = []

    def bucket(self, name):
        return self

    def blob(self, name):
        store = self

        class Blob:
            def download_as_bytes(self):
                return store.objects[name]

            def upload_from_string(self, value, *, content_type, if_generation_match):
                assert if_generation_match == 0
                if name in store.objects:
                    error = RuntimeError("simulated GCS precondition failure")
                    error.code = 412
                    raise error
                store.objects[name] = value.encode()
                store.writes.append(name)

            def download_to_filename(self, path):
                Path(path).write_bytes(store.objects[name])

            def upload_from_filename(self, path):
                store.objects[name] = Path(path).read_bytes()
                store.writes.append(name)

        return Blob()


@pytest.mark.parametrize("prior", [None, b"invalid json", b'{"completed":false,"returncode":1}'])
def test_duplicate_start_reports_prior_status_without_overwriting(prior):
    store = MemoryStore({})
    claim_output(store, "gs://bucket/run", "smoke")
    original = dict(store.objects)
    if prior is not None:
        store.objects["run/receipt.json"] = prior
    with pytest.raises(SystemExit) as error:
        claim_output(store, "gs://bucket/run", "smoke")
    message = str(error.value)
    assert "Output prefix already claimed" in message
    assert "gs://bucket/run/training.log" in message
    assert "new output prefix" in message
    if prior and prior.startswith(b"{"):
        assert "completed=False, returncode=1" in message
    else:
        assert "No readable final receipt" in message
    assert store.objects["run/claimed.json"] == original["run/claimed.json"]
    assert store.writes == ["run/claimed.json"]


def test_claim_permission_error_is_not_misreported_as_duplicate():
    class Denied(MemoryStore):
        def bucket(self, name):
            error = RuntimeError("simulated permission failure")
            error.code = 403
            raise error

    with pytest.raises(RuntimeError, match="permission"):
        claim_output(Denied({}), "gs://bucket/run", "smoke")


def inputs(name="train.jsonl", content=b"synthetic preference"):
    manifest = json.dumps({"files": {name: hashlib.sha256(content).hexdigest()}}).encode()
    store = MemoryStore({"bundle/manifest.json": manifest, "bundle/" + name: content})
    return store, hashlib.sha256(manifest).hexdigest()


def test_manifest_stages_exact_files_only(tmp_path):
    store, digest = inputs()
    store.objects["bundle/unreviewed.jsonl"] = b"must not enter training"
    fetch_manifest(store, "gs://bucket/bundle", digest, tmp_path / "data", "manifest.json")
    assert sorted(p.name for p in (tmp_path / "data").iterdir()) == ["manifest.json", "train.jsonl"]


def test_snapshot_upload_uses_exact_objects_and_publishes_manifest_last(tmp_path):
    store = MemoryStore({})
    (tmp_path / "model.safetensors").write_bytes(b"synthetic weights stub")
    (tmp_path / "healthops-snapshot.json").write_text(
        json.dumps({"files": {"model.safetensors": "fixture"}})
    )
    (tmp_path / "unlisted-cache").write_bytes(b"do not upload")
    upload_snapshot(store, tmp_path, "gs://bucket/model")
    assert store.writes == ["model/model.safetensors", "model/healthops-snapshot.json"]


@pytest.mark.parametrize("tamper", ["manifest", "content", "path"])
def test_remote_input_tampering_fails(tmp_path, tamper):
    store, digest = inputs("../escape" if tamper == "path" else "train.jsonl")
    if tamper == "manifest":
        digest = "0" * 64
    if tamper == "content":
        store.objects["bundle/train.jsonl"] = b"changed after approval"
    with pytest.raises(ValueError):
        fetch_manifest(store, "gs://bucket/bundle", digest, tmp_path / "data", "manifest.json")
    assert not (tmp_path / "escape").exists()


def test_logs_are_refreshed_and_receipt_is_not_published_early(tmp_path):
    store = MemoryStore({})
    log = tmp_path / "training.log"
    log.write_text("start")
    (tmp_path / "receipt.json").write_text("{}")
    cache = {}
    upload_results(store, tmp_path, "gs://bucket/run", cache)
    upload_results(store, tmp_path, "gs://bucket/run", cache)
    assert store.writes == ["run/training.log"]
    log.write_text("start\nfailed")
    upload_results(store, tmp_path, "gs://bucket/run", cache)
    assert store.objects["run/training.log"] == b"start\nfailed"
    assert "run/receipt.json" not in store.objects


def test_exit_zero_without_checkpoint_is_not_a_completed_smoke(tmp_path):
    (tmp_path / "exit.json").write_text('{"training_completed":true}')
    (tmp_path / "preflight.json").write_text("{}")
    assert receipt(tmp_path, 0, {})["completed"] is False
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "checkpoints" / "adapter.pt").write_bytes(b"synthetic checkpoint stub")
    result = receipt(tmp_path, 0, {})
    assert result["completed"] is True
    assert result["restore_verified"] is False
    (tmp_path / "restore-check.json").write_text('{"restore_verified":true}')
    assert receipt(tmp_path, 0, {})["restore_verified"] is True
    assert receipt(tmp_path, 1, {})["restore_verified"] is False
    assert receipt(tmp_path, 1, {})["completed"] is False


def test_output_aliases_never_upload_targets_or_count_as_checkpoints(tmp_path, caplog):
    output = tmp_path / "output"
    output.mkdir()
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "secret").write_text("must not be uploaded")
    (output / "training.log").write_text("synthetic log")
    (output / "latest.log").symlink_to("training.log")
    (output / "external").symlink_to(outside / "secret")
    (output / "checkpoints").symlink_to(outside, target_is_directory=True)
    (output / "broken").symlink_to("missing")
    (output / "loop").symlink_to(output, target_is_directory=True)
    store = MemoryStore({})
    upload_results(store, output, "gs://bucket/run", {})
    assert store.writes == ["run/training.log"]
    assert receipt(output, 0, {})["checkpoint_files"] == {}
    assert "latest.log" in caplog.text
    assert "external" in caplog.text


def test_periodic_upload_failure_can_recover_without_aborting(tmp_path, monkeypatch):
    import writer_dpo.cloud as cloud

    original = cloud.upload_results
    monkeypatch.setattr(cloud, "upload_results", lambda *args: (_ for _ in ()).throw(OSError()))
    assert sync_results(MemoryStore({}), tmp_path, "gs://bucket/run", {}) == "OSError"
    monkeypatch.setattr(cloud, "upload_results", original)
    (tmp_path / "training.log").write_text("still training")
    store = MemoryStore({})
    assert sync_results(store, tmp_path, "gs://bucket/run", {}) is None
    assert store.objects["run/training.log"] == b"still training"


def test_final_sync_failure_still_publishes_failed_receipt(tmp_path, monkeypatch):
    import writer_dpo.cloud as cloud

    calls = []

    def fail(*args):
        calls.append(1)
        raise OSError("synthetic upload failure")

    monkeypatch.setattr(cloud, "upload_results", fail)
    monkeypatch.setattr(cloud.time, "sleep", lambda seconds: None)
    (tmp_path / "exit.json").write_text('{"training_completed":true}')
    (tmp_path / "preflight.json").write_text("{}")
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "checkpoints" / "adapter.pt").write_bytes(b"fixture")
    store = MemoryStore({})
    result = finalize_results(store, tmp_path, "gs://bucket/run", 0, {"bundle": "fixture"})
    assert len(calls) == 3
    assert result["completed"] is False
    assert result["artifacts_synced"] is False
    assert result["artifact_sync_error"] == "OSError"
    assert json.loads(store.objects["run/receipt.json"]) == result


def test_final_receipt_is_published_last(tmp_path):
    (tmp_path / "training.log").write_text("finished")
    store = MemoryStore({})
    result = finalize_results(store, tmp_path, "gs://bucket/run", 1, {})
    assert store.writes == ["run/training.log", "run/receipt.json"]
    assert result["artifacts_synced"] is True
    assert result["completed"] is False


def test_vertex_spec_binds_four_gpus_and_finite_lifetime():
    spec = job_spec(
        "us-docker.pkg.dev/p/r/i@sha256:" + "a" * 64,
        "training@example.com",
        "gs://bucket/bundle",
        "b" * 64,
        "gs://bucket/model",
        "c" * 64,
        "gs://bucket/run",
    )
    job = spec["jobSpec"]
    worker = job["workerPoolSpecs"][0]
    assert worker["machineSpec"] == {
        "machineType": "a2-highgpu-4g",
        "acceleratorType": "NVIDIA_TESLA_A100",
        "acceleratorCount": 4,
    }
    assert worker["replicaCount"] == "1"
    assert job["scheduling"]["maxWaitDuration"] == "1800s"
    assert job["scheduling"]["timeout"] == "7200s"
    assert job["scheduling"]["strategy"] == "FLEX_START"


@pytest.mark.parametrize("uri", ["https://bucket/x", "gs://bucket", "gs://bucket/../secret"])
def test_invalid_storage_paths_rejected(uri):
    with pytest.raises(ValueError):
        gcs_path(uri)
