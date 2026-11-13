"""Exercise evaluation gates and registration retries without cloud access."""

import copy
import json
from urllib.error import HTTPError

import pytest

from writer_dpo import registry as r


def response(ident, prompt="frozen"):
    return dict(
        id=ident, messages_sha256=prompt, raw_output="Synthetic answer", completion_tokens=12
    )


def judgment(ident, grade="pass", claims=0, omissions=0):
    return dict(
        id=ident,
        grade=grade,
        unsupported_claims=claims,
        omitted_requested_facts=omissions,
        reason="Reviewed against synthetic evidence",
        output_sha256=r.digest(b"Synthetic answer"),
    )


def test_review_binds_ids_inputs_outputs_and_requires_complete_grades():
    expected = {"v1": "frozen", "v2": "frozen"}
    reviews = [judgment(i) for i in expected]
    outputs = [response(i) for i in expected]
    assert set(r.review_scores(reviews, outputs, expected)) == set(expected)
    for mutate in [
        lambda x: x.pop(),
        lambda x: x.__setitem__(1, copy.deepcopy(x[0])),
        lambda x: x[0].update(grade="pending"),
        lambda x: x[0].update(output_sha256="changed"),
        lambda x: x[0].update(unsupported_claims=-1),
    ]:
        changed = copy.deepcopy(reviews)
        mutate(changed)
        with pytest.raises(ValueError):
            r.review_scores(changed, outputs, expected)
    for key, value in [
        ("messages_sha256", "wrong"),
        ("completion_tokens", 800),
        ("raw_output", ""),
    ]:
        changed = copy.deepcopy(outputs)
        changed[0][key] = value
        with pytest.raises(ValueError):
            r.review_scores(reviews, changed, expected)


def test_selection_prefers_base_then_earlier_checkpoint_and_ignores_engineering():
    models = {k: {"v": judgment("v"), "eng": judgment("eng")} for k in ["base", "11", "22"]}
    assert r.choose_checkpoint(models, {"eng"})[0] == "base"
    models["base"]["v"] = judgment("v", "fail")
    models["11"]["eng"] = judgment("eng", "fail", 9, 9)
    assert r.choose_checkpoint(models, {"eng"})[0] == "11"


def test_final_gate_rejects_failure_claims_and_partial_regression_but_reports_legacy():
    models = {k: {i: judgment(i) for i in ["new", "legacy"]} for k in ["base", "selected"]}
    models["selected"]["legacy"] = judgment("legacy", "fail", 1, 1)
    assert r.final_gate(models, ["new"])["passed"]
    for grade, claims, omissions in [
        ("fail", 0, 0),
        ("pass", 1, 0),
        ("partial", 0, 0),
        ("pass", 0, 1),
    ]:
        models["selected"]["new"] = judgment("new", grade, claims, omissions)
        assert not r.final_gate(models, ["new"])["passed"]


class FakeCloud:
    root = "projects/demo/locations/us-central1"

    def __init__(self):
        self.objects = {}
        self.models = {}
        self.operations = {}
        self.uploads = []
        self.lose_response = False

    def blob(self, uri):
        if uri not in self.objects:
            raise HTTPError(uri, 404, "absent", {}, None)
        return self.objects[uri]

    def put_once(self, uri, raw):
        if uri in self.objects and self.objects[uri] != raw:
            raise ValueError("immutable")
        self.objects[uri] = raw

    def claim(self, uri, raw):
        if uri in self.objects:
            return False
        self.objects[uri] = raw
        return True

    def vertex(self, path, body=None):
        assert "endpoints" not in path and "deploy" not in path
        if body is not None:
            assert path == self.root + "/models:upload"
            self.uploads.append(body)
            name = self.root + "/operations/upload"
            self.operations[name] = {"name": name, "done": False}
            if self.lose_response:
                raise TimeoutError("Upload response lost")
            return self.operations[name]
        if path in self.operations:
            return self.operations[path]
        if path not in self.models:
            raise HTTPError(path, 404, "absent", {}, None)
        return self.models[path]


def test_register_first_version_and_resume_operation_without_deployment():
    cloud = FakeCloud()
    policy = {"model_id": "writer", "data_version": "synthetic"}
    uri = "gs://bucket/release"
    sha = "a" * 64
    assert not r.register(cloud, policy, uri, sha)["registered"]
    request = cloud.uploads[0]
    assert request["modelId"] == "writer" and "parentModel" not in request
    assert "artifactUri" not in request["model"] and "containerSpec" not in request["model"]
    assert "default" not in request["model"]["versionAliases"]
    name = next(iter(cloud.operations))
    cloud.operations[name].update(
        done=True, response={"model": cloud.root + "/models/writer", "modelVersionId": "1"}
    )
    result = r.register(cloud, policy, uri, sha)
    assert result["registered"] and result["version_id"] == "1" and not result["deploy"]
    assert len(cloud.uploads) == 1


def test_new_version_uses_parent_and_known_alias_is_idempotent():
    cloud = FakeCloud()
    policy = {"model_id": "writer", "data_version": "synthetic"}
    parent = cloud.root + "/models/writer"
    cloud.models[parent] = {"name": parent}
    uri = "gs://bucket/release"
    sha = "b" * 64
    r.register(cloud, policy, uri, sha)
    assert cloud.uploads[0]["parentModel"] == parent and "modelId" not in cloud.uploads[0]
    cloud.models[parent + "@eval-" + sha[:24]] = {
        "name": parent,
        "versionId": "2",
        "labels": {"release": sha[:32]},
        "metadata": {"release_sha256": sha, "release_uri": uri},
    }
    assert r.register(cloud, policy, uri, sha)["version_id"] == "2"
    assert len(cloud.uploads) == 1


def test_lost_upload_response_never_blindly_retries():
    cloud = FakeCloud()
    cloud.lose_response = True
    policy = {"model_id": "writer", "data_version": "x"}
    with pytest.raises(TimeoutError):
        r.register(cloud, policy, "gs://b/release", "c" * 64)
    with pytest.raises(RuntimeError, match="unknown"):
        r.register(cloud, policy, "gs://b/release", "c" * 64)
    assert len(cloud.uploads) == 1


def test_concurrent_upload_loses_claim_and_does_not_create_version():
    cloud = FakeCloud()
    cloud.claim = lambda *args: False
    with pytest.raises(RuntimeError, match="Another caller"):
        r.register(cloud, {"model_id": "writer", "data_version": "x"}, "gs://b/release", "d" * 64)
    assert not cloud.uploads


def test_transport_forbids_deployment_and_resource_mutation():
    client = r.GoogleRegistry("demo", "us-central1")
    for path in [
        client.root + "/endpoints",
        client.root + "/models/writer",
        client.root + "/endpoints/123:deployModel",
    ]:
        with pytest.raises(ValueError):
            client.vertex(path, {})


@pytest.fixture
def experiment(tmp_path):
    def put(name, value):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.encode(value))

    def put_lines(name, values):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(v) + "\n" for v in values))

    policy = {
        "source_hashes": {},
        "auto_deploy": False,
        "registration_authorized": True,
        "candidate_steps": [11, 22],
        "expected_inputs": {
            "validation": {"v": "frozen"},
            "test": {"t": "frozen", "old": "frozen"},
        },
        "engineering_validation_ids": [],
        "final_test_ids": ["t"],
        "final_test_policy": "no regressions",
        "project": "demo",
        "region": "us-central1",
        "model_id": "writer",
        "data_version": "synthetic",
        "release_prefix": "gs://bucket/releases",
        "training_output_uri": "gs://bucket/train",
        "custom_job": "projects/demo/locations/us-central1/customJobs/123",
    }
    put("registry/policy.json", policy)
    execution = {
        "bundle_sha256": "bundle",
        "snapshot_sha256": r.digest(b"snapshot"),
        "snapshot_uri": "gs://bucket/base",
    }
    put("training/execution-plan.json", execution)
    put("training/R1-v6-config.json", {"model_id": "Qwen/test", "model_revision": "pinned"})
    put("training/training-plan.json", {"train_count": 11})
    put("training/bundle/manifest.json", {"files": {}, "counts": {"train": 11}})
    put(
        "training/observed-output/train/receipt.json",
        {
            "completed": True,
            "artifacts_synced": True,
            "returncode": 0,
            "inputs": {"bundle": "bundle", "snapshot": execution["snapshot_sha256"]},
            "checkpoint_files": {"checkpoints/step_11/policy/weights/x.distcp": "weight-hash"},
        },
    )
    put("training/observed-output/train/state-audit-step11.json", {"state_preserved": True})
    put(
        "training/observed-output/generation/step11/adapter-check.json",
        {
            "finite": True,
            "nonzero_lora_b": True,
            "files": {
                "adapter_config.json": r.digest(b"config"),
                "adapter_model.safetensors": r.digest(b"weights"),
            },
        },
    )
    put(
        "registry/validation-review.json",
        {
            "completed": True,
            "reviewer": "fixture reviewer",
            "models": {
                "base": [judgment("v", "fail")],
                "11": [judgment("v")],
                "22": [judgment("v")],
            },
        },
    )
    for model in ["base", "11", "22"]:
        name = (
            "baseline/base-responses.jsonl"
            if model == "base"
            else f"generation/step{model}/adapted-responses.jsonl"
        )
        put_lines("training/observed-output/" + name, [response("v")])
    r.select(tmp_path)
    for model in ["base", "selected"]:
        put_lines(f"registry/final-test-{model}.jsonl", [response("t"), response("old")])
    put(
        "registry/final-test-review.json",
        {
            "completed": True,
            "reviewer": "fixture reviewer",
            "selection_sha256": r.file_hash(tmp_path / "registry/selection.json"),
            "models": {k: [judgment("t"), judgment("old")] for k in ["base", "selected"]},
        },
    )
    put(
        "registry/generation-receipt.json",
        {
            "completed": True,
            "selected": "11",
            "test_plan_sha256": r.file_hash(tmp_path / "registry/final-test-plan.json"),
            "response_hashes": {
                f"final-test-{k}.jsonl": r.file_hash(tmp_path / f"registry/final-test-{k}.jsonl")
                for k in ["base", "selected"]
            },
            "inputs": {
                "bundle": "bundle",
                "snapshot": execution["snapshot_sha256"],
                "adapter_check": r.file_hash(
                    tmp_path / "training/observed-output/generation/step11/adapter-check.json"
                ),
            },
        },
    )
    return tmp_path


def test_full_review_to_registration_gate_and_content_tampering(experiment):
    result = r.finalize(experiment)
    assert result == {"ready_for_registration": True, "selected_step": 11, "deploy": False}
    path = experiment / "registry/final-test-selected.jsonl"
    path.write_text(path.read_text().replace("Synthetic", "Changed"))
    with pytest.raises(ValueError, match="file changed"):
        r.finalize(experiment, True, client=object())


def test_failed_or_mismatched_final_review_prevents_any_cloud_call(experiment):
    path = experiment / "registry/final-test-review.json"
    doc = r.read(path)
    doc["models"]["selected"][0]["grade"] = "fail"
    path.write_bytes(r.encode(doc))
    with pytest.raises(ValueError, match="did not pass"):
        r.finalize(experiment, True, client=object())
    doc["models"]["selected"][0]["grade"] = "pass"
    doc["selection_sha256"] = "wrong"
    path.write_bytes(r.encode(doc))
    with pytest.raises(ValueError, match="different checkpoint"):
        r.finalize(experiment, True, client=object())


def test_finalization_archives_artifacts_configuration_and_reports(experiment):
    cloud = FakeCloud()
    cloud.models[cloud.root + "/customJobs/123"] = {"state": "JOB_STATE_SUCCEEDED"}
    cloud.objects["gs://bucket/train/train/receipt.json"] = (
        experiment / "training/observed-output/train/receipt.json"
    ).read_bytes()
    cloud.objects["gs://bucket/base/healthops-snapshot.json"] = b"snapshot"
    for path in (experiment / "training/observed-output").rglob("*"):
        if path.is_file():
            relative = path.relative_to(experiment / "training/observed-output").as_posix()
            cloud.objects["gs://bucket/train/" + relative] = path.read_bytes()
    for name, raw in [
        ("adapter_config.json", b"config"),
        ("adapter_model.safetensors", b"weights"),
    ]:
        cloud.objects["gs://bucket/train/generation/step11/adapter/" + name] = raw
    result = r.finalize(experiment, True, cloud)
    prefix = result["release_uri"]
    manifest = json.loads(cloud.objects[prefix + "/manifest.json"])
    for name in [
        "training-config.json",
        "evaluation-summary.json",
        "base-model.json",
        "adapter/adapter_model.safetensors",
        "checkpoint-state.json",
    ]:
        assert manifest["files"][name] == r.digest(cloud.objects[prefix + "/" + name])
    assert not result["registered"] and len(cloud.uploads) == 1 and not result["deploy"]


def test_replay_rejects_wrong_adapter_before_loading_a_gpu_model(experiment, monkeypatch):
    from writer_dpo import registry_replay

    monkeypatch.setattr(registry_replay, "verify_snapshot", lambda *args: None)
    snapshot = experiment / "snapshot"
    snapshot.mkdir()
    (snapshot / "healthops-snapshot.json").write_bytes(b"snapshot")
    adapter = experiment / "adapter"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_bytes(b"wrong")
    with pytest.raises(ValueError, match="adapter changed"):
        registry_replay.replay_inputs(experiment, snapshot, adapter)


@pytest.mark.parametrize("field", ["selected", "test_plan_sha256", "inputs"])
def test_generation_receipt_is_bound_to_selection_and_model_artifacts(experiment, field):
    path = experiment / "registry/generation-receipt.json"
    value = r.read(path)
    value[field] = "incorrect"
    path.write_bytes(r.encode(value))
    with pytest.raises(ValueError):
        r.finalize(experiment, True, client=object())


def test_missing_review_cannot_register_or_load_cloud_credentials(experiment):
    (experiment / "registry/final-test-review.json").unlink()
    with pytest.raises(FileNotFoundError):
        r.finalize(experiment, True, client=object())
