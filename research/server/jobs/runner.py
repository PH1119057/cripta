from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(os.environ.get("CRIPTA_JOB_ROOT", "/data/cripta/jobs"))
DATASETS = Path(os.environ.get("CRIPTA_DATASET_ROOT", "/data/cripta/datasets/raw"))
REPORTS = Path(os.environ.get("CRIPTA_REPORT_ROOT", "/srv/cripta-share/reports/jobs"))


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def next_job() -> Path | None:
    queued = ROOT / "queued"
    running = ROOT / "running"
    for source in sorted((p for p in queued.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime):
        target = running / source.name
        try:
            source.replace(target)
            return target
        except FileNotFoundError:
            continue
    return None


def execute(job: Path) -> None:
    status_path = job / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    manifest = status["manifest"]
    bundle = job / "bundle"
    dataset = DATASETS / manifest["dataset"]["period"]
    report = REPORTS / job.name
    report.mkdir(parents=True, exist_ok=False)
    started = int(time.time())
    status.update({"status": "running", "started_at_epoch": started, "report_path": f"jobs/{job.name}"})
    atomic_json(status_path, status)
    env = {
        **os.environ,
        "CRIPTA_JOB_ID": job.name,
        "CRIPTA_DATASET_DIR": str(dataset),
        "CRIPTA_REPORT_DIR": str(report),
        "CRIPTA_PARAMETERS_JSON": json.dumps(manifest["parameters"], ensure_ascii=False),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }
    outcome = "completed"
    exit_code: int | None = None
    error = ""
    try:
        with (report / "stdout.log").open("wb") as stdout, (report / "stderr.log").open("wb") as stderr:
            result = subprocess.run(
                ["/usr/bin/python3", "-I", str(bundle / manifest["entrypoint"])],
                cwd=bundle,
                env=env,
                stdout=stdout,
                stderr=stderr,
                timeout=manifest["timeout_seconds"],
                check=False,
            )
        exit_code = result.returncode
        if exit_code != 0:
            outcome = "failed"
            error = f"entrypoint exited with code {exit_code}"
    except subprocess.TimeoutExpired:
        outcome = "failed"
        error = f"timeout after {manifest['timeout_seconds']} seconds"
    except Exception as exc:
        outcome = "failed"
        error = f"{type(exc).__name__}: {exc}"
    finished = int(time.time())
    status.update({
        "status": outcome,
        "finished_at_epoch": finished,
        "duration_seconds": finished - started,
        "exit_code": exit_code,
        "error": error,
    })
    atomic_json(status_path, status)
    atomic_json(report / "result.json", status)
    destination = ROOT / outcome / job.name
    job.replace(destination)


def main() -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    for state in ("queued", "running", "completed", "failed"):
        (ROOT / state).mkdir(parents=True, exist_ok=True)
    while True:
        job = next_job()
        if job is None:
            time.sleep(3)
            continue
        execute(job)


if __name__ == "__main__":
    main()
