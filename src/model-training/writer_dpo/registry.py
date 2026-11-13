"""Finalize reviewed Writer evaluations and register a referenced model; never deploy it."""

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from writer_dpo.data import file_hash, fingerprint


def read(path):
    return json.loads(Path(path).read_text())


def encode(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2) + "\n").encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def write_once(path, value):
    path = Path(path)
    raw = encode(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError(f"Immutable record differs: {path.name}")
    else:
        with path.open("xb") as stream:
            stream.write(raw)


def jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def review_scores(reviews, responses, expected):
    """Bind human judgments to complete, exact frozen inputs and observed output text."""
    if len(reviews) != len(expected) or len(responses) != len(expected):
        raise ValueError("Incomplete evaluation")
    reviewed = {r["id"]: r for r in reviews}
    observed = {r.get("id", r.get("case_id")): r for r in responses}
    if set(reviewed) != set(expected) or set(observed) != set(expected):
        raise ValueError("Duplicate, missing or unexpected evaluation IDs")
    for ident, prompt_hash in expected.items():
        row, output = reviewed[ident], observed[ident]
        if output["messages_sha256"] != prompt_hash:
            raise ValueError("Evaluation input changed")
        if not output.get("raw_output", "").strip() or output.get("completion_tokens", 800) >= 800:
            raise ValueError("Empty or possibly truncated generation")
        if row["output_sha256"] != digest(output["raw_output"].encode()):
            raise ValueError("Reviewed output changed")
        if row["grade"] not in {"pass", "partial", "fail"} or not row.get("reason"):
            raise ValueError("Unresolved semantic review")
        for key in ("unsupported_claims", "omitted_requested_facts"):
            if type(row[key]) is not int or row[key] < 0:
                raise ValueError("Semantic issue counts must be nonnegative integers")
    return reviewed


def score(rows):
    return (
        sum(r["grade"] == "fail" for r in rows),
        sum(r["unsupported_claims"] for r in rows),
        sum(r["omitted_requested_facts"] for r in rows),
    )


def choose_checkpoint(models, excluded):
    """Original validation rule: failures, claims, omissions, then base/earlier step."""
    scores = {k: score([r for i, r in v.items() if i not in excluded]) for k, v in models.items()}
    winner = min(scores, key=lambda k: (*scores[k], 0 if k == "base" else int(k)))
    return winner, scores


def final_gate(models, final_ids):
    """Legacy tests are reported, never used to choose or replace the selected checkpoint."""
    reasons = []
    ranks = {"pass": 0, "partial": 1, "fail": 2}
    for ident in final_ids:
        base, candidate = models["base"][ident], models["selected"][ident]
        if candidate["grade"] == "fail" or candidate["unsupported_claims"]:
            reasons.append(f"{ident}: final-test failure or unsupported claim")
        if ranks[candidate["grade"]] > ranks[base["grade"]] or any(
            candidate[k] > base[k] for k in ("unsupported_claims", "omitted_requested_facts")
        ):
            reasons.append(f"{ident}: regression against the original base")
    return {"passed": not reasons, "reasons": reasons}


def reviewed_document(path):
    doc = read(path)
    if doc.get("completed") is not True or not doc.get("reviewer", "").strip():
        raise ValueError("Human semantic review is incomplete")
    return doc


def initialize(experiment):
    """Freeze registration policy before any final-test selection feedback."""
    execution = read(experiment / "training/execution-plan.json")
    plan = read(experiment / "training/training-plan.json")
    inventory = read(experiment / "inventory124.json")
    groups = {}
    for split in ("validation", "test"):
        groups[split] = {
            r["id"]: fingerprint(r["messages"]) for r in inventory["rows"] if r["split"] == split
        }
    final_ids = [r["id"] for r in jsonl(experiment / "test.frozen-inputs.jsonl")]
    excluded = [
        r["id"]
        for r in inventory["rows"]
        if r["split"] == "validation" and r.get("regression_only")
    ]
    policy = {
        "schema": "healthops-writer-registration-v1",
        "project": execution["project"],
        "region": execution["region"],
        "model_id": "healthops-writer-qwen3-8b",
        "release_prefix": f"gs://{execution['bucket']}/writer-dpo/registry/releases",
        "training_output_uri": execution["output_uri"],
        "custom_job": read(experiment / "training/job-submitted.json")["name"],
        "data_version": plan["data_version"],
        "candidate_steps": plan["generation_steps"],
        "expected_inputs": groups,
        "final_test_ids": final_ids,
        "engineering_validation_ids": excluded,
        "final_test_policy": (
            "No failed cases, unsupported claims or per-case regression; partial answers "
            "are allowed only without regression. Legacy tests are report-only."
        ),
        "registration_authorized": True,
        "auto_deploy": False,
        "source_hashes": {
            name: file_hash(experiment / name)
            for name in (
                "training/execution-plan.json",
                "training/training-plan.json",
                "training/R1-v6-config.json",
                "training/bundle/manifest.json",
                "inventory124.json",
                "test.frozen-inputs.jsonl",
            )
        },
    }
    if len(groups["validation"]) != 48 or len(groups["test"]) != 65 or len(final_ids) != 20:
        raise ValueError("Unexpected experiment evaluation inventory")
    if not set(final_ids) <= groups["test"].keys():
        raise ValueError("Final test IDs are not in the frozen test inventory")
    write_once(experiment / "registry/policy.json", policy)
    return policy


def policy_for(experiment):
    policy = read(experiment / "registry/policy.json")
    if policy["auto_deploy"] is not False or policy["registration_authorized"] is not True:
        raise ValueError("Registration-only authorization required")
    for name, expected in policy["source_hashes"].items():
        if file_hash(experiment / name) != expected:
            raise ValueError(f"Frozen experiment changed: {name}")
    return policy


def training_receipt(experiment):
    execution = read(experiment / "training/execution-plan.json")
    receipt = read(experiment / "training/observed-output/train/receipt.json")
    if not (
        receipt.get("completed") is True
        and receipt.get("artifacts_synced") is True
        and receipt.get("returncode") == 0
        and receipt.get("inputs")
        == {"bundle": execution["bundle_sha256"], "snapshot": execution["snapshot_sha256"]}
    ):
        raise ValueError("Training receipt does not match a completed experiment")
    return receipt


def select(experiment):
    policy = policy_for(experiment)
    training_receipt(experiment)
    folder = experiment / "registry"
    review = reviewed_document(folder / "validation-review.json")
    expected_models = {"base", *map(str, policy["candidate_steps"])}
    if set(review["models"]) != expected_models:
        raise ValueError("Review every planned checkpoint and original base")
    observed = experiment / "training/observed-output"
    models, sources = {}, {}
    for model in sorted(expected_models):
        path = observed / (
            "baseline/base-responses.jsonl"
            if model == "base"
            else f"generation/step{model}/adapted-responses.jsonl"
        )
        models[model] = review_scores(
            review["models"][model], jsonl(path), policy["expected_inputs"]["validation"]
        )
        sources[str(path.relative_to(experiment))] = file_hash(path)
    winner, scores = choose_checkpoint(models, set(policy["engineering_validation_ids"]))
    record = {
        "schema": "healthops-writer-checkpoint-selection-v1",
        "selected": winner,
        "scores": scores,
        "selection_split": "validation",
        "test_used_for_selection": False,
        "policy_sha256": file_hash(folder / "policy.json"),
        "validation_review_sha256": file_hash(folder / "validation-review.json"),
        "response_hashes": sources,
    }
    write_once(folder / "selection.json", record)
    test_plan = {
        "selection_sha256": file_hash(folder / "selection.json"),
        "selected": winner,
        "expected_inputs": policy["expected_inputs"]["test"],
        "final_test_ids": policy["final_test_ids"],
        "instruction": (
            "Generate original base and this selected checkpoint only. Bind this plan hash "
            "in generation-receipt.json before semantic review. Do not switch checkpoints "
            "after testing."
        ),
    }
    write_once(folder / "final-test-plan.json", test_plan)
    return read(folder / "selection.json")


def prepare_release(experiment):
    policy = policy_for(experiment)
    folder = experiment / "registry"
    selection = read(folder / "selection.json")
    # Recompute selection from original reviewed validation outputs, never trust a chosen step.
    if select(experiment) != selection:
        raise ValueError("Checkpoint selection changed")
    if selection["selected"] == "base":
        raise ValueError("Validation selected the base; no fine-tuned version qualifies")
    generation = read(folder / "generation-receipt.json")
    if not (
        generation.get("completed") is True
        and generation.get("test_plan_sha256") == file_hash(folder / "final-test-plan.json")
        and generation.get("selected") == selection["selected"]
    ):
        raise ValueError("Final generation is not bound to the frozen selection")
    review = reviewed_document(folder / "final-test-review.json")
    if review.get("selection_sha256") != file_hash(folder / "selection.json"):
        raise ValueError("Final review refers to a different checkpoint selection")
    models = {}
    for model in ("base", "selected"):
        path = folder / f"final-test-{model}.jsonl"
        if generation["response_hashes"][path.name] != file_hash(path):
            raise ValueError("Final generation file changed")
        models[model] = review_scores(
            review["models"][model], jsonl(path), policy["expected_inputs"]["test"]
        )
    gate = final_gate(models, policy["final_test_ids"])
    if not gate["passed"]:
        raise ValueError("Final evaluation did not pass: " + "; ".join(gate["reasons"]))
    step = selection["selected"]
    observed = experiment / "training/observed-output"
    audit_path = observed / f"train/state-audit-step{step}.json"
    if read(audit_path).get("state_preserved") is not True:
        raise ValueError("Recoverable training state has not been verified")
    check_path = observed / f"generation/step{step}/adapter-check.json"
    check = read(check_path)
    execution = read(experiment / "training/execution-plan.json")
    if generation.get("inputs") != {
        "bundle": execution["bundle_sha256"],
        "snapshot": execution["snapshot_sha256"],
        "adapter_check": file_hash(check_path),
    }:
        raise ValueError("Final generation model artifacts do not match the selection")
    required = {"adapter_config.json", "adapter_model.safetensors"}
    if (
        not check.get("finite")
        or not check.get("nonzero_lora_b")
        or not required <= check["files"].keys()
    ):
        raise ValueError("Adapter export is not verified")
    receipt = training_receipt(experiment)
    prefix = f"checkpoints/step_{step}/"
    if not any(k.startswith(prefix) and k.endswith(".distcp") for k in receipt["checkpoint_files"]):
        raise ValueError("Selected checkpoint missing from training receipt")
    report = {
        "passed": True,
        "policy": policy["final_test_policy"],
        "selected_step": int(step),
        "selection_sha256": file_hash(folder / "selection.json"),
        "final_test": {
            k: dict(Counter(v[i]["grade"] for i in policy["final_test_ids"]))
            for k, v in models.items()
        },
        "legacy_regression": {
            k: dict(Counter(r["grade"] for i, r in v.items() if i not in policy["final_test_ids"]))
            for k, v in models.items()
        },
        "legacy_regression_affects_selection": False,
        "checkpoint_uri": f"{policy['training_output_uri']}/train/checkpoints/step_{step}",
        "reviewer": review["reviewer"],
        "deploy": False,
    }
    files = {
        "training-config.json": (experiment / "training/R1-v6-config.json").read_bytes(),
        "training-plan.json": (experiment / "training/training-plan.json").read_bytes(),
        "dataset-manifest.json": (experiment / "training/bundle/manifest.json").read_bytes(),
        "training-receipt.json": (observed / "train/receipt.json").read_bytes(),
        "checkpoint-state.json": audit_path.read_bytes(),
        "adapter-check.json": check_path.read_bytes(),
        "evaluation-summary.json": encode(report),
    }
    for name in (
        "policy.json",
        "selection.json",
        "validation-review.json",
        "final-test-plan.json",
        "final-test-review.json",
        "generation-receipt.json",
        "final-test-base.jsonl",
        "final-test-selected.jsonl",
    ):
        files[name] = (folder / name).read_bytes()
    for name in selection["response_hashes"]:
        files["validation/" + name.replace("/", "__")] = (experiment / name).read_bytes()
    execution = read(experiment / "training/execution-plan.json")
    config = read(experiment / "training/R1-v6-config.json")
    files["base-model.json"] = encode(
        {
            "model_id": config["model_id"],
            "revision": config["model_revision"],
            "snapshot_uri": execution["snapshot_uri"],
            "snapshot_sha256": execution["snapshot_sha256"],
            "composition": "Load the pinned base with adapter/. Weights are not merged.",
        }
    )
    return policy, step, files, check["files"]


class GoogleRegistry:
    """Small REST client: GCS, Model upload/read, operation read; no deployment methods."""

    def __init__(self, project, region):
        if not re.fullmatch(r"[a-z0-9-]+", project) or not re.fullmatch(r"[a-z0-9-]+", region):
            raise ValueError("Invalid project or region")
        self.root = f"projects/{project}/locations/{region}"
        self.host = f"https://{region}-aiplatform.googleapis.com/v1/"

    def request(self, url, body=None, raw=False):
        token = subprocess.check_output(
            ["gcloud", "auth", "print-access-token"], text=True, stderr=subprocess.PIPE, timeout=45
        ).strip()
        payload = body if isinstance(body, bytes) else encode(body) if body is not None else None
        headers = {
            "Authorization": "Bearer " + token,
            "Content-Type": "application/octet-stream"
            if isinstance(body, bytes)
            else "application/json",
        }
        with urlopen(Request(url, data=payload, headers=headers), timeout=60) as response:
            value = response.read()
        return value if raw else json.loads(value or b"{}")

    def vertex(self, path, body=None):
        allowed_get = re.fullmatch(
            r"projects/[a-z0-9-]+/locations/[a-z0-9-]+/(models/[^/]+|(?:models/[^/]+/)?operations/[^/]+|customJobs/[0-9]+)",
            path,
        )
        if body is not None and path != self.root + "/models:upload":
            raise ValueError("Only model registration writes are permitted")
        if body is None and not allowed_get:
            raise ValueError("Unsupported registry read")
        return self.request(self.host + path, body)

    @staticmethod
    def location(uri):
        match = re.fullmatch(r"gs://([a-z0-9._-]+)/(.+)", uri)
        if not match or ".." in match[2].split("/"):
            raise ValueError("Expected safe GCS object URI")
        return match[1], match[2]

    def blob(self, uri):
        bucket, name = self.location(uri)
        return self.request(
            f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/"
            f"{quote(name, safe='')}?alt=media",
            raw=True,
        )

    def put_once(self, uri, raw):
        bucket, name = self.location(uri)
        query = urlencode({"uploadType": "media", "name": name, "ifGenerationMatch": 0})
        try:
            self.request(
                f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o?{query}", raw
            )
        except HTTPError as exc:
            if exc.code != 412:
                raise
        if self.blob(uri) != raw:
            raise ValueError("Existing immutable registry object differs")

    def claim(self, uri, raw):
        """An exclusive upload intent prevents concurrent callers creating two versions."""
        bucket, name = self.location(uri)
        query = urlencode({"uploadType": "media", "name": name, "ifGenerationMatch": 0})
        try:
            self.request(
                f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o?{query}", raw
            )
            return True
        except HTTPError as exc:
            if exc.code != 412:
                raise
            if self.blob(uri) != raw:
                raise ValueError("Existing registration intent differs") from exc
            return False


def optional_get(call):
    try:
        return call()
    except HTTPError as exc:
        if exc.code != 404:
            raise
        return None


def register(client, policy, release_uri, release_hash):
    """Resume recorded operations; never blindly retry an ambiguous version upload."""
    parent = client.root + "/models/" + policy["model_id"]
    alias = "eval-" + release_hash[:24]
    known = optional_get(lambda: client.vertex(parent + "@" + alias))
    if known:
        if (
            known.get("labels", {}).get("release") != release_hash[:32]
            or known.get("metadata", {}).get("release_sha256") != release_hash
            or known.get("metadata", {}).get("release_uri") != release_uri
        ):
            raise ValueError("Registry alias points to different release")
        result = {
            "registered": True,
            "model": known["name"],
            "version_id": known["versionId"],
            "alias": alias,
            "deploy": False,
        }
        client.put_once(release_uri + "/registration-result.json", encode(result))
        return result
    operation_uri = release_uri + "/registration-operation.json"
    saved = optional_get(lambda: client.blob(operation_uri))
    if saved:
        operation = client.vertex(json.loads(saved)["name"])
    else:
        intent_uri = release_uri + "/registration-intent.json"
        if optional_get(lambda: client.blob(intent_uri)):
            raise RuntimeError(
                "Upload outcome unknown; inspect the model/operation before retrying"
            )
        exists = optional_get(lambda: client.vertex(parent))
        model = {
            "displayName": "HealthOps Writer Qwen3-8B",
            "description": (
                "Evaluated base-plus-LoRA reference. No serving container or endpoint configured."
            ),
            "versionAliases": [alias],
            "versionDescription": (
                f"Reviewed release {release_hash}; artifacts, configuration and evaluations: "
                f"{release_uri}/manifest.json"
            ),
            "labels": {
                "component": "healthops-writer",
                "release": release_hash[:32],
                "deployment": "none",
            },
            "metadataSchemaUri": release_uri + "/metadata-schema.yaml",
            "metadata": {
                "release_uri": release_uri,
                "release_sha256": release_hash,
                "data_version": policy["data_version"],
                "training_config_uri": release_uri + "/training-config.json",
                "evaluation_summary_uri": release_uri + "/evaluation-summary.json",
                "base_model_uri": release_uri + "/base-model.json",
            },
        }
        request = {
            "model": model,
            **({"parentModel": parent} if exists else {"modelId": policy["model_id"]}),
        }
        if not client.claim(intent_uri, encode(request)):
            raise RuntimeError("Another caller claimed this upload; resume its operation later")
        operation = client.vertex(client.root + "/models:upload", request)
        client.put_once(operation_uri, encode({"name": operation["name"]}))
    if operation.get("error"):
        raise RuntimeError("Model upload failed: " + json.dumps(operation["error"]))
    if not operation.get("done"):
        return {
            "registered": False,
            "operation": operation["name"],
            "alias": alias,
            "deploy": False,
        }
    response = operation["response"]
    result = {
        "registered": True,
        "model": response["model"],
        "version_id": response["modelVersionId"],
        "alias": alias,
        "deploy": False,
    }
    client.put_once(release_uri + "/registration-result.json", encode(result))
    return result


def finalize(experiment, execute=False, client=None):
    policy, step, files, adapter_hashes = prepare_release(experiment)
    if not execute:
        return {"ready_for_registration": True, "selected_step": int(step), "deploy": False}
    client = client or GoogleRegistry(policy["project"], policy["region"])
    if client.vertex(policy["custom_job"])["state"] != "JOB_STATE_SUCCEEDED":
        raise ValueError("Training/evaluation CustomJob has not succeeded")
    # Revalidate original remote receipt, base manifest and adapter bytes before registration.
    if (
        client.blob(policy["training_output_uri"] + "/train/receipt.json")
        != files["training-receipt.json"]
    ):
        raise ValueError("Remote training receipt changed")
    selection = json.loads(files["selection.json"])
    for name, expected in selection["response_hashes"].items():
        relative = Path(name).relative_to("training/observed-output").as_posix()
        if digest(client.blob(policy["training_output_uri"] + "/" + relative)) != expected:
            raise ValueError("Validation responses differ from the training job")
    for relative, archived in (
        (f"train/state-audit-step{step}.json", "checkpoint-state.json"),
        (f"generation/step{step}/adapter-check.json", "adapter-check.json"),
    ):
        if client.blob(policy["training_output_uri"] + "/" + relative) != files[archived]:
            raise ValueError("Remote checkpoint/export audit changed")
    base = json.loads(files["base-model.json"])
    snapshot = client.blob(base["snapshot_uri"] + "/healthops-snapshot.json")
    if digest(snapshot) != base["snapshot_sha256"]:
        raise ValueError("Base snapshot manifest changed")
    files["base-snapshot-manifest.json"] = snapshot
    for name in ("adapter_config.json", "adapter_model.safetensors"):
        raw = client.blob(f"{policy['training_output_uri']}/generation/step{step}/adapter/{name}")
        if digest(raw) != adapter_hashes[name]:
            raise ValueError("Adapter bytes differ from evaluated export")
        files["adapter/" + name] = raw
    files["metadata-schema.yaml"] = (
        b"title: HealthOpsWriterRelease\ntype: object\nproperties:\n"
        b"  release_uri:\n    type: string\n  release_sha256:\n    type: string\n"
        b"  data_version:\n    type: string\n"
        b"  training_config_uri:\n    type: string\n"
        b"  evaluation_summary_uri:\n    type: string\n"
        b"  base_model_uri:\n    type: string\n"
    )
    manifest = {
        "schema": "healthops-writer-registry-release-v1",
        "selected_step": int(step),
        "files": {k: digest(v) for k, v in files.items()},
        "data_version": policy["data_version"],
    }
    release_hash = digest(encode(manifest))
    release_uri = policy["release_prefix"] + "/" + release_hash
    files["manifest.json"] = encode(manifest)
    for name, raw in files.items():
        client.put_once(release_uri + "/" + name, raw)
    result = register(client, policy, release_uri, release_hash)
    result["release_uri"] = release_uri
    (experiment / "registry/result.json").write_bytes(encode(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["initialize", "select", "finalize", "status"])
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument(
        "--execute", action="store_true", help="Register after evaluation gates; never deploy"
    )
    args = parser.parse_args()
    experiment = args.experiment.resolve()
    if args.action == "initialize":
        result = initialize(experiment)
    elif args.action == "select":
        result = select(experiment)
    elif args.action == "finalize":
        result = finalize(experiment, args.execute)
    else:
        policy_for(experiment)
        required = [
            "validation-review.json",
            "selection.json",
            "final-test-plan.json",
            "generation-receipt.json",
            "final-test-review.json",
            "final-test-base.jsonl",
            "final-test-selected.jsonl",
        ]
        result = {
            "missing": [p for p in required if not (experiment / "registry" / p).exists()],
            "registered": False,
            "deploy": False,
        }
        if (experiment / "registry/result.json").exists():
            result.update(read(experiment / "registry/result.json"))
        result["checked_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
