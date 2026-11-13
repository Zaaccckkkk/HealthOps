"""Stage immutable GCS inputs, run NeMo, and persist results from a Vertex worker."""

import argparse
import json
import logging
import os
import re
import signal
import subprocess
import time
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from writer_dpo.data import file_hash, write_json

LOG = logging.getLogger(__name__)


def artifact_files(root):
    """Ignore aliases, including directory links; never publish their targets."""
    if root.is_symlink():
        raise ValueError("Artifact root must not be a symlink")
    for directory, dirs, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in sorted(dirs + files):
            path = parent / name
            if path.is_symlink():
                LOG.warning("Skipping artifact symlink: %s", path.relative_to(root))
                if name in dirs:
                    dirs.remove(name)
            elif name in files and path.is_file():
                yield path


def gcs_path(uri):
    parsed = urlparse(uri)
    key = parsed.path.lstrip("/")
    if parsed.scheme != "gs" or not parsed.netloc or not key or parsed.query or parsed.fragment:
        raise ValueError("Expected a bucket and object prefix in a gs:// URI")
    if ".." in PurePosixPath(key).parts:
        raise ValueError("Parent paths are not allowed")
    return parsed.netloc, key.rstrip("/")


def safe_file(root, name):
    if not name or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
        raise ValueError("Unsafe manifest path")
    target = root / name
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Manifest path escapes its destination")
    return target


def fetch_manifest(client, uri, expected_hash, destination, manifest_name):
    """Only files listed by a hash-pinned manifest may be downloaded."""
    bucket, prefix = gcs_path(uri)
    destination.mkdir(parents=True, exist_ok=False)
    manifest_path = destination / manifest_name
    client.bucket(bucket).blob(f"{prefix}/{manifest_name}").download_to_filename(manifest_path)
    if file_hash(manifest_path) != expected_hash:
        raise ValueError("Remote manifest differs from the approved manifest hash")
    manifest = json.loads(manifest_path.read_text())
    if not manifest.get("files"):
        raise ValueError("Empty input manifest")
    for name, digest in manifest["files"].items():
        path = safe_file(destination, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        client.bucket(bucket).blob(f"{prefix}/{name}").download_to_filename(path)
        if file_hash(path) != digest:
            raise ValueError(f"Remote artifact checksum mismatch: {name}")
    return manifest


def upload_results(client, output, uri, previous):
    """Periodic uploads are provisional; only a final receipt marks a complete run."""
    bucket, prefix = gcs_path(uri)
    for path in sorted(artifact_files(output)):
        relative = path.relative_to(output).as_posix()
        if relative == "receipt.json":
            continue
        stat = path.stat()
        signature = (stat.st_size, stat.st_mtime_ns)
        if previous.get(relative) == signature:
            continue
        client.bucket(bucket).blob(f"{prefix}/{relative}").upload_from_filename(path)
        previous[relative] = signature


def sync_results(client, output, uri, previous):
    """A failed provisional sync must not terminate the training subprocess."""
    try:
        upload_results(client, output, uri, previous)
    except Exception as exc:
        # Avoid logging storage exception messages that may contain credentials.
        LOG.warning("Artifact sync failed (%s); will retry", type(exc).__name__)
        return type(exc).__name__
    return None


def finalize_results(client, output, uri, returncode, input_hashes):
    """Publish an honest receipt even when checkpoint/log publication fails."""
    try:
        result = receipt(output, returncode, input_hashes)
    except Exception as exc:
        result = {
            "completed": False,
            "returncode": returncode,
            "inputs": input_hashes,
            "restore_verified": False,
            "receipt_error": type(exc).__name__,
        }
    error = None
    for attempt in range(3):
        # Always refresh after exit: provisional checkpoints may be incomplete.
        error = sync_results(client, output, uri, {})
        if error is None:
            break
        if attempt < 2:
            time.sleep(5)
    result["artifacts_synced"] = error is None
    if error:
        result.update(completed=False, restore_verified=False, artifact_sync_error=error)
    write_json(output / "receipt.json", result)
    bucket, prefix = gcs_path(uri)
    # Separate from the directory upload so a bad artifact cannot suppress it.
    client.bucket(bucket).blob(f"{prefix}/receipt.json").upload_from_filename(
        output / "receipt.json"
    )
    return result


def receipt(output, returncode, input_hashes):
    checkpoint_files = [
        p for p in artifact_files(output) if p.relative_to(output).parts[0] == "checkpoints"
    ]
    exit_path = output / "exit.json"
    completed = (
        returncode == 0
        and exit_path.exists()
        and json.loads(exit_path.read_text()).get("training_completed") is True
        and (output / "preflight.json").exists()
        and bool(checkpoint_files)
    )
    restore_path = output / "restore-check.json"
    restored = (
        completed
        and restore_path.exists()
        and json.loads(restore_path.read_text()).get("restore_verified") is True
    )
    return {
        "completed": completed,
        "returncode": returncode,
        "inputs": input_hashes,
        "checkpoint_files": {str(p.relative_to(output)): file_hash(p) for p in checkpoint_files},
        "restore_verified": bool(restored),
        "note": "Functional restore is reported separately; answer quality remains unverified.",
    }


def claim_output(client, uri, mode):
    """Keep exclusive ownership, but explain a restart without masking its first failure."""
    bucket, prefix = gcs_path(uri)
    try:
        client.bucket(bucket).blob(prefix + "/claimed.json").upload_from_string(
            json.dumps({"mode": mode, "claimed_at": time.time()}),
            content_type="application/json",
            if_generation_match=0,
        )
    except Exception as exc:
        if getattr(exc, "code", None) != 412:
            raise
        status = "No readable final receipt; the earlier attempt may still be active."
        try:
            prior = json.loads(
                client.bucket(bucket).blob(prefix + "/receipt.json").download_as_bytes()
            )
            completed, code = prior.get("completed"), prior.get("returncode")
            if type(completed) is bool and type(code) is int:
                status = f"Existing receipt: completed={completed}, returncode={code}."
        except Exception:
            pass
        raise SystemExit(
            f"Output prefix already claimed: {uri}. {status} "
            f"Inspect {uri}/training.log for the original failure. "
            "This duplicate start will not overwrite artifacts or restart training. "
            "Use a new output prefix for an intentional retry."
        ) from None


def main():
    from google.cloud import storage

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle-uri", "bundle-sha256", "snapshot-uri", "snapshot-sha256", "output-uri"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--mode", choices=["smoke", "train", "restore-check"], default="smoke")
    parser.add_argument("--workdir", type=Path, default=Path("/workspace/.local/writer"))
    args = parser.parse_args()
    if os.environ.get("HEALTHOPS_WANDB_SECRET_VERSION"):
        from writer_dpo.tracking import inject_wandb_secret

        inject_wandb_secret(os.environ["HEALTHOPS_WANDB_SECRET_VERSION"])
    for digest in (args.bundle_sha256, args.snapshot_sha256):
        if not re.fullmatch("[a-f0-9]{64}", digest):
            raise ValueError("Input manifest SHA-256 is required")
    inputs = [gcs_path(args.bundle_uri), gcs_path(args.snapshot_uri)]
    destination = gcs_path(args.output_uri)
    for bucket, prefix in inputs:
        if destination[0] == bucket and (
            destination[1] == prefix
            or destination[1].startswith(prefix + "/")
            or prefix.startswith(destination[1] + "/")
        ):
            raise ValueError("Outputs must not overlap immutable inputs")
    os.environ["HEALTHOPS_RUN_IDENTITY"] = args.output_uri.rstrip("/")
    output = args.workdir / "output"
    client = storage.Client()
    claim_output(client, args.output_uri, args.mode)
    args.workdir.mkdir(parents=True, exist_ok=False)
    process = None
    previous = {}
    code = 1
    started = time.monotonic()

    def interrupted(signum, frame):
        raise InterruptedError(f"Worker received signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        fetch_manifest(
            client, args.bundle_uri, args.bundle_sha256, args.workdir / "bundle", "manifest.json"
        )
        fetch_manifest(
            client,
            args.snapshot_uri,
            args.snapshot_sha256,
            args.workdir / "snapshot",
            "healthops-snapshot.json",
        )
        command = [
            "uv",
            "run",
            "--frozen",
            "python",
            "-m",
            "writer_dpo.launch",
            "--bundle",
            str(args.workdir / "bundle"),
            "--snapshot",
            str(args.workdir / "snapshot"),
            "--nemo-root",
            "/opt/nemo-rl",
            "--output",
            str(output),
            "--mode",
            args.mode,
            "--execute",
        ]
        process = subprocess.Popen(command, cwd="/opt/nemo-rl", start_new_session=True)
        while process.poll() is None:
            if time.monotonic() - started > 6600:
                raise TimeoutError("Worker execution budget exhausted; preserving outputs")
            sync_results(client, output, args.output_uri, previous)
            time.sleep(15)
        code = process.returncode
    finally:
        # Ignore repeat termination while preserving logs and partial checkpoints.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        output.mkdir(parents=True, exist_ok=True)
        result = finalize_results(
            client,
            output,
            args.output_uri,
            code,
            {"bundle": args.bundle_sha256, "snapshot": args.snapshot_sha256},
        )
    if not result["completed"]:
        raise SystemExit(code or 1)


if __name__ == "__main__":
    main()
