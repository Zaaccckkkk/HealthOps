"""Fetch an immutable HF snapshot explicitly, before renting training GPUs."""

import argparse
import json
from pathlib import Path

from writer_dpo.data import file_hash, write_json

CONFIG = Path(__file__).with_name("config.json")


def upload_snapshot(client, path, uri):
    """Upload exact manifest objects without bucket listing; publish the manifest last."""
    from writer_dpo.cloud import gcs_path, safe_file

    bucket, prefix = gcs_path(uri)
    manifest = json.loads((path / "healthops-snapshot.json").read_text())
    for name in [*manifest["files"], "healthops-snapshot.json"]:
        client.bucket(bucket).blob(f"{prefix}/{name}").upload_from_filename(safe_file(path, name))


def verify_snapshot(path, config):
    manifest = json.loads((path / "healthops-snapshot.json").read_text())
    if (manifest["model_id"], manifest["revision"]) != (
        config["model_id"],
        config["model_revision"],
    ):
        raise ValueError("Model snapshot does not match the pinned model revision")
    if not manifest.get("files") or not any(n.endswith(".safetensors") for n in manifest["files"]):
        raise ValueError("Full HF weights required; tokenizer-only and GGUF are not trainable here")
    for name, expected in manifest["files"].items():
        target = (path / name).resolve()
        if not target.is_relative_to(path.resolve()) or file_hash(target) != expected:
            raise ValueError("Snapshot content changed")
    return manifest


def main():
    from huggingface_hub import snapshot_download

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--gcs-uri", help="Optionally upload the verified snapshot with runtime ADC"
    )
    args = parser.parse_args()
    if ".local" not in args.output.resolve().parts:
        raise ValueError("Model artifacts must stay under .local")
    config = json.loads(CONFIG.read_text())
    snapshot_download(
        repo_id=config["model_id"],
        revision=config["model_revision"],
        local_dir=args.output,
        allow_patterns=["*.json", "*.safetensors", "*.jinja", "*.txt"],
    )
    files = {
        str(p.relative_to(args.output)): file_hash(p)
        for p in args.output.rglob("*")
        if p.is_file() and ".cache" not in p.parts and p.name != "healthops-snapshot.json"
    }
    write_json(
        args.output / "healthops-snapshot.json",
        {
            "model_id": config["model_id"],
            "revision": config["model_revision"],
            "files": files,
        },
    )
    verify_snapshot(args.output, config)
    if args.gcs_uri:
        from google.cloud import storage

        upload_snapshot(storage.Client(), args.output, args.gcs_uri)
    print("Pinned full snapshot verified; no training started")


if __name__ == "__main__":
    main()
