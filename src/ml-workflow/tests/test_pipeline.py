"""The pipeline compiles to the intended graph and the submit script never contacts the cloud
unless asked: prepare -> one GPU training job per window (parallel) -> compare."""

import json

import pytest
from kfp import compiler

from pipeline import submit
from pipeline.pipeline import PIPELINE_NAME, reconciliation_pipeline, worker_pool


def _find(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            yield obj[key]
        for value in obj.values():
            yield from _find(value, key)
    elif isinstance(obj, list):
        for value in obj:
            yield from _find(value, key)


@pytest.fixture(scope="module")
def spec(tmp_path_factory):
    path = tmp_path_factory.mktemp("spec") / "pipeline.json"
    compiler.Compiler().compile(reconciliation_pipeline, str(path))
    return json.loads(path.read_text())


def test_graph_is_prepare_then_parallel_train_then_compare(spec):
    assert spec["pipelineInfo"]["name"] == PIPELINE_NAME
    tasks = {t["taskInfo"]["name"]: t for t in spec["root"]["dag"]["tasks"].values()}
    assert set(tasks) == {"prepare daily table", "train-per-window", "compare windows"}
    assert tasks["prepare daily table"].get("dependentTasks") is None
    assert tasks["compare windows"]["dependentTasks"] == ["for-loop-1"]
    loop = tasks["train-per-window"]
    assert loop["dependentTasks"] == ["custom-training-job"]
    assert loop["parameterIterator"]["items"]["inputParameter"] == "pipelinechannel--windows"
    assert loop["iteratorPolicy"]["parallelismLimit"] == 1  # one GPU of quota by default


def test_parallelism_is_set_at_compile_time(tmp_path, monkeypatch):
    out = tmp_path / "p.json"
    assert submit.main(["--compile-only", "--out", str(out), "--parallelism", "2"]) == 0
    spec = json.loads(out.read_text())
    loop = [t for t in spec["root"]["dag"]["tasks"].values() if "parameterIterator" in t][0]
    assert loop["iteratorPolicy"]["parallelismLimit"] == 2


def test_defaults_and_gpu_spec(spec):
    params = spec["root"]["inputDefinitions"]["parameters"]
    assert params["windows"]["defaultValue"] == ["14", "30"]
    assert params["machine_type"]["defaultValue"] == "g2-standard-4"
    assert params["accelerator_type"]["defaultValue"] == "NVIDIA_L4"
    assert params["model"]["defaultValue"] == "transformer"
    assert params["smoke"]["parameterType"] == "STRING"  # a boolean cannot feed an env value
    # The training step's worker pool carries the accelerator; prepare and compare do not.
    pools = [p for p in _find(spec, "worker_pool_specs")]
    assert pools, "worker_pool_specs expected in the compiled spec"
    text = json.dumps(spec)
    assert "accelerator_type" in text and "--prepare-only" in text and "healthops-compare" in text
    assert "--run-suffix" in text and '{\\"window\\": ' in text  # JSON-escaped config
    # Vertex rejects an empty env value, so nothing optional is passed as an env entry.
    assert "WANDB_ENTITY" not in text and "WANDB_API_KEY" in text


def test_worker_pool_shapes():
    cpu = worker_pool("img", ["a"], {"X": "1", "EMPTY": ""}, "n1-standard-8")
    assert cpu[0]["machine_spec"] == {
        "machine_type": "n1-standard-8",
        "accelerator_type": "ACCELERATOR_TYPE_UNSPECIFIED",
        "accelerator_count": 0,
    }
    assert cpu[0]["container_spec"]["env"] == [{"name": "X", "value": "1"}]  # empty dropped
    gpu = worker_pool("img", ["a"], {}, "g2-standard-4", "NVIDIA_L4", 1)
    assert gpu[0]["machine_spec"]["accelerator_type"] == "NVIDIA_L4"
    assert gpu[0]["machine_spec"]["accelerator_count"] == 1
    assert gpu[0]["replica_count"] == 1


def test_submit_compile_only_writes_spec_without_cloud(tmp_path, monkeypatch):
    out = tmp_path / "missing-folder" / "p.json"  # the output folder is created on demand
    monkeypatch.delenv("IMAGE", raising=False)
    assert submit.main(["--compile-only", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["pipelineInfo"]["name"] == PIPELINE_NAME


def test_submit_parameters_validate_windows():
    args = submit.parse_args(["--image", "img", "--windows", "14,30", "--smoke", "--users", "200"])
    params = submit.parameters(args, "run1", "key")
    assert params["windows"] == ["14", "30"]
    assert params["smoke"] == "true" and params["users"] == 200 and params["run_id"] == "run1"
    assert params["wandb_api_key"] == "key" and "wandb_entity" not in params
    cpu = submit.parse_args(["--image", "img", "--accelerator-type", "none"])
    cpu_params = submit.parameters(cpu, "run2", "key")
    assert cpu_params["accelerator_type"] == "ACCELERATOR_TYPE_UNSPECIFIED"
    assert cpu_params["accelerator_count"] == 0
    bad = submit.parse_args(["--image", "img", "--windows", "14,abc"])
    with pytest.raises(SystemExit):
        submit.parameters(bad, "run1", "")


def test_submit_requires_image_to_submit(tmp_path):
    with pytest.raises(SystemExit):
        submit.main(["--out", str(tmp_path / "p.json")])


def test_submit_refuses_to_run_without_a_wandb_key(tmp_path, monkeypatch):
    monkeypatch.setattr(submit, "wandb_key", lambda project, secret: "")
    with pytest.raises(SystemExit, match="no W&B key"):
        submit.main(["--image", "img", "--out", str(tmp_path / "p.json")])
