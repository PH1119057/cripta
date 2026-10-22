#!/usr/bin/env python3
"""CRIPTA read-only market research workflow: sequential bounded worker only.

Effective writer: cripta; no DB/Exchange/service/Git operations.
Read: published source scripts + immutable raw trade sources.
Write/create/rename: only /data/cripta/research/runs/microstructure_joint_4f_20261023
and exact child dirs delegated to published materializer/analyzer. No delete.
No root or broader grants; no output overwrites. Source snapshot pinned per run.
"""
import argparse
import hashlib
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import time

BASE = pathlib.Path("/srv/cripta/source_checkout")
SCRIPTS = BASE / "research/microstructure/experiments"
ROOT = pathlib.Path(
    "/data/cripta/research/runs/microstructure_joint_4f_20261023"
)
COHORT = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
PROGRAMS = {
    "materialize": "trade_4factor_continuous_30day_v2.py",
    "qc": "trade_4factor_continuous_independent_qc_v2.py",
    "analyze": "trade_4factor_joint_price_response_30day_v2.py",
}


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as inp:
        for chunk in iter(lambda: inp.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def dump_new(path, value):
    tmp = path.with_name(path.name + ".part")
    with tmp.open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, sort_keys=True, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    tmp.replace(path)


def memory_available_kib():
    for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1])
    raise ValueError("NO_MEM_AVAILABLE_SIGNAL")


def child_rss_kib(pid):
    try:
        for line in pathlib.Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except FileNotFoundError:
        return None
    return None


def run_child(workdir, title, script, args):
    import resource

    command = [
        sys.executable, "-B",
        str(workdir / "source_snapshot" / PROGRAMS[script]), *args,
    ]
    stdout_path = workdir / "logs" / (title + ".stdout.log")
    stderr_path = workdir / "logs" / (title + ".stderr.log")
    started = time.monotonic()
    peak_rss = 0
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        process = subprocess.Popen(
            command, cwd=BASE, stdin=subprocess.DEVNULL,
            stdout=stdout, stderr=stderr, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        while True:
            sample = child_rss_kib(process.pid)
            if sample is not None:
                peak_rss = max(peak_rss, sample)
            try:
                exit_code = process.wait(timeout=15)
                break
            except subprocess.TimeoutExpired:
                progress = [
                    s for s in stderr_path.read_text(errors="replace").splitlines()
                    if s.startswith("{")
                ]
                print(json.dumps({
                    "stage": title, "pid": process.pid,
                    "elapsed_seconds": round(time.monotonic() - started, 1),
                    "peak_rss_kib_sampled": peak_rss,
                    "last_child_progress": progress[-1] if progress else None,
                    "heartbeat": True,
                }), flush=True)
        duration = round(time.monotonic() - started, 2)
    peak_rss = max(peak_rss, resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    status = {
        "stage": title,
        "exit_code": exit_code,
        "elapsed_seconds": duration,
        "peak_rss_kib_sampled": peak_rss,
        "worker_pid": process.pid,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    }
    print(json.dumps({"job_stage_finished": status}), flush=True)
    if exit_code != 0:
        raise RuntimeError("FAILED_CHILD_STAGE " + title + " " + str(exit_code))
    return status


def workflow(mode):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("ACTOR_NOT_CRIPTA")
    if mode not in ("smoke", "month"):
        raise ValueError("INVALID_MODE")
    if not os.access(ROOT, os.W_OK | os.X_OK):
        raise PermissionError("WORKFLOW_ROOT_DENIED")
    main_sha = (BASE / ".git/refs/heads/main").read_text().strip()
    work = ROOT / ("batch_" + mode + "_v3")
    if work.exists():
        raise FileExistsError("NO_OVERWRITE_EXISTING_BATCH " + str(work))
    if mode == "month":
        smoke = json.loads((ROOT / "batch_smoke_v3" / "result.json").read_text())
        if smoke.get("status") != "SMOKE_PASS":
            raise ValueError("BOUNDED_SMOKE_GATE_MISSING")
        if smoke["max_sampled_worker_rss_kib"] > 800000:
            raise MemoryError("SMOKE_WORKER_RSS_OVER_800MB_LIMIT")
        if memory_available_kib() < 1400000:
            raise MemoryError("AVAILABLE_MEMORY_BELOW_1_4GB_LIMIT")
    work.mkdir(mode=0o2770, exist_ok=False)
    for dirname in ("source_snapshot", "logs", "results"):
        (work / dirname).mkdir(mode=0o2770, exist_ok=False)
    script = pathlib.Path(__file__).resolve()
    shutil.copyfile(script, work / "source_snapshot" / "orchestrator.py")
    program_hashes = {}
    for role, name in PROGRAMS.items():
        original = SCRIPTS / name
        shutil.copyfile(original, work / "source_snapshot" / name)
        program_hashes[role] = sha256(original)
    (work / "source_commit.txt").write_text(main_sha + "\n")
    (work / "command.txt").write_text(
        sys.executable + " -B " + str(script) + " --mode " + mode + "\n"
    )
    (work / "source_sha256.txt").write_text(sha256(script) + "\n")
    (work / "environment.txt").write_text(
        "actor_uid=" + str(os.geteuid()) + "\n"
        "python=" + sys.executable + "\n"
        "python_version=" + sys.version.split()[0] + "\n"
        "system=" + platform.system() + "\n"
    )
    steps = []
    errors = []
    symbol_set = ("INJUSDT",) if mode == "smoke" else COHORT
    days = 2 if mode == "smoke" else 30
    spec = {
        "status": "PREPARED", "mode": mode,
        "source_commit": main_sha, "actor_uid": os.geteuid(),
        "source_hashes": program_hashes,
        "symbols": symbol_set, "days": days, "worker_count": 1,
        "runtime_code_not_changed": True,
        "source_receipt_clock": "UNKNOWN",
    }
    dump_new(work / "run_manifest.json", spec)
    dump_new(work / "input_provenance.json", {
        "source": "BYBIT_PUBLIC_TRADES_ARCHIVE",
        "raw_root": "/data/cripta/datasets/raw/20260518_20260816",
        "manifest": (
            "research/microstructure/"
            "TRADE_TAPE_RAW_30DAY_150_SOURCE_MANIFEST_2026-10-21.json"
        ),
        "code_sha256": program_hashes,
    })
    (work / "logs" / "progress.jsonl").touch(exist_ok=False)
    try:
        for sym in symbol_set:
            phys = run_child(
                work, sym + "_materialize", "materialize",
                ["--symbol", sym, "--days", str(days)],
            )
            steps.append(phys)
            check = run_child(
                work, sym + "_independent_decimal_qc", "qc",
                ["--symbol", sym, "--days", str(days)],
            )
            steps.append(check)
            stdout_content = pathlib.Path(check["stdout"]).read_text()
            lines = [x for x in stdout_content.splitlines() if x.startswith("{")]
            if len(lines) != 1:
                raise ValueError("QC_RECEIPT_CARDINALITY_" + sym)
            qc = json.loads(lines[0])
            if (
                qc["status"] != "FULL_4FACTOR_DECIMAL_INDEPENDENT_QC_PASS_NOT_RESEARCH_READY"
                or qc["symbol"] != sym or qc["days"] != days
                or qc["rows"] != 1440 * days
            ):
                raise ValueError("INDEPENDENT_QC_GATE_FAILED_" + sym)
            dump_new(work / "results" / (sym + "_qc_receipt.json"), qc)
            if mode == "month":
                analyze = run_child(
                    work, sym + "_joint_analysis", "analyze",
                    ["--symbol", sym],
                )
                steps.append(analyze)
            print(json.dumps({
                "stage": "SYMBOL_QC_CONFIRMED",
                "symbol": sym, "completed": len(
                    [x for x in steps if x["stage"].endswith("independent_decimal_qc")]
                ), "total": len(symbol_set),
                "qc_sha256": qc["feature_sha256"],
            }), flush=True)
    except Exception as error:
        errors.append(type(error).__name__ + ": " + str(error))
        dump_new(work / "results" / "failure.json", {
            "status": "FAILED", "errors": errors,
            "completed_stage_count": len(steps),
        })
        raise
    peak = max((x["peak_rss_kib_sampled"] for x in steps), default=0)
    result = {
        "status": "SMOKE_PASS" if mode == "smoke" else "MONTH30_QC_PASS_ANALYSIS_DONE",
        "source_commit": main_sha,
        "mode": mode, "symbols": symbol_set, "days": days,
        "complete_stage_count": len(steps),
        "steps": steps,
        "max_sampled_worker_rss_kib": peak,
        "source_hashes": program_hashes,
        "first_period_only": True,
        "research_ready": False,
        "live_trading_permission": False,
    }
    dump_new(work / "result.json", result)
    dump_new(work / "results" / "index.json", {
        "status": result["status"],
        "result_sha256": sha256(work / "result.json"),
        "source_commit": main_sha,
    })
    print(json.dumps({
        "status": result["status"], "mode": mode, "symbols": symbol_set,
        "days": days, "complete_stage_count": len(steps),
        "max_sampled_worker_rss_kib": peak,
        "run_dir": str(work),
    }), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("smoke", "month"), required=True)
    args = parser.parse_args()
    workflow(args.mode)
