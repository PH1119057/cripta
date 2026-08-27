from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import time
import zipfile
from pathlib import Path, PurePosixPath

INCOMING = Path(os.environ.get("CRIPTA_JOB_INCOMING", "/srv/cripta-share/incoming/jobs"))
ROOT = Path(os.environ.get("CRIPTA_JOB_ROOT", "/data/cripta/jobs"))
JOB_ID = re.compile(r"^C2-R[0-9]{3,6}-[A-Z0-9][A-Z0-9_-]{1,40}$")
PERIOD = re.compile(r"^[0-9]{8}_[0-9]{8}$")
SYMBOL = re.compile(r"^[A-Z0-9]+USDT$")
MAX_FILES = 500
MAX_UNPACKED = 200 * 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def validate_manifest(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("job.json must contain an object")
    allowed = {"job_id", "title", "line", "entrypoint", "dataset", "parameters", "dependencies", "timeout_seconds", "notes"}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unknown job.json fields: {sorted(unknown)}")
    job_id = value.get("job_id")
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise ValueError("invalid job_id")
    if not isinstance(value.get("title"), str) or not 3 <= len(value["title"]) <= 160:
        raise ValueError("invalid title")
    if value.get("line") not in {"current", "legacy-recheck"}:
        raise ValueError("line must be current or legacy-recheck")
    entrypoint = value.get("entrypoint")
    if not isinstance(entrypoint, str) or not entrypoint.endswith(".py"):
        raise ValueError("entrypoint must be a relative .py file")
    entry = PurePosixPath(entrypoint)
    if entry.is_absolute() or ".." in entry.parts:
        raise ValueError("unsafe entrypoint")
    dataset = value.get("dataset")
    if not isinstance(dataset, dict) or set(dataset) != {"period", "symbols"}:
        raise ValueError("dataset requires only period and symbols")
    if not isinstance(dataset["period"], str) or not PERIOD.fullmatch(dataset["period"]):
        raise ValueError("invalid dataset period")
    symbols = dataset["symbols"]
    if not isinstance(symbols, list) or not symbols or any(not isinstance(x, str) or not SYMBOL.fullmatch(x) for x in symbols):
        raise ValueError("invalid dataset symbols")
    if not isinstance(value.get("parameters"), dict):
        raise ValueError("parameters must be an object")
    dependencies = value.get("dependencies")
    if not isinstance(dependencies, list) or any(not isinstance(x, str) or len(x) > 100 for x in dependencies):
        raise ValueError("invalid dependencies")
    timeout = value.get("timeout_seconds", 86400)
    if not isinstance(timeout, int) or not 60 <= timeout <= 604800:
        raise ValueError("invalid timeout_seconds")
    value["timeout_seconds"] = timeout
    return value


def inspect_archive(archive: zipfile.ZipFile) -> None:
    members = archive.infolist()
    if len(members) > MAX_FILES:
        raise ValueError("too many files")
    if sum(member.file_size for member in members) > MAX_UNPACKED:
        raise ValueError("unpacked bundle is too large")
    for member in members:
        path = PurePosixPath(member.filename)
        if path.is_absolute() or ".." in path.parts or "\\" in member.filename:
            raise ValueError(f"unsafe archive path: {member.filename}")
        mode = member.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ValueError(f"symlink is forbidden: {member.filename}")


def reject(bundle: Path, fingerprint: str, reason: str) -> None:
    target = ROOT / "intake" / "rejected" / f"{bundle.stem}-{fingerprint[:12]}"
    target.mkdir(parents=True, exist_ok=True)
    shutil.move(str(bundle), target / bundle.name)
    atomic_json(target / "receipt.json", {"status": "rejected", "sha256": fingerprint, "reason": reason, "epoch": int(time.time())})


def accept(bundle: Path) -> None:
    fingerprint = sha256(bundle)
    staging = ROOT / "intake" / "staging" / fingerprint
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(bundle) as archive:
            inspect_archive(archive)
            archive.extractall(staging / "bundle")
        manifest_path = staging / "bundle" / "job.json"
        if not manifest_path.is_file():
            raise ValueError("job.json is missing at bundle root")
        manifest = validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
        entrypoint = staging / "bundle" / str(manifest["entrypoint"])
        if not entrypoint.is_file():
            raise ValueError("entrypoint does not exist in bundle")
        job_id = str(manifest["job_id"])
        destination = ROOT / "queued" / job_id
        if destination.exists():
            raise ValueError(f"job_id already exists: {job_id}")
        receipt = {"status": "queued", "job_id": job_id, "sha256": fingerprint, "accepted_at_epoch": int(time.time()), "manifest": manifest}
        atomic_json(staging / "status.json", receipt)
        staging.replace(destination)
        accepted = ROOT / "intake" / "accepted"
        accepted.mkdir(parents=True, exist_ok=True)
        shutil.move(str(bundle), accepted / f"{job_id}-{fingerprint[:12]}.zip")
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        reject(bundle, fingerprint, f"{type(exc).__name__}: {exc}")


def main() -> None:
    for path in (ROOT / "intake" / "accepted", ROOT / "intake" / "rejected", ROOT / "intake" / "staging"):
        path.mkdir(parents=True, exist_ok=True)
    INCOMING.mkdir(parents=True, exist_ok=True)
    while True:
        for bundle in sorted(INCOMING.glob("*.zip")):
            try:
                accept(bundle)
            except FileNotFoundError:
                pass
        time.sleep(5)


if __name__ == "__main__":
    main()
