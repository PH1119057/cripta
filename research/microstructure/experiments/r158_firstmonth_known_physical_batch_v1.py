#!/usr/bin/env python3
"""CRIPTA first-month research job coordinator, one bounded worker at a time.

First month Bybit only, May 18-Jun 16 2026 UTC, five exact instruments. Research
physical variants in source snapshot; never touch Exchange/DB/Strategy/LIVE.
Stages: price-month (#19/#22), book-smoke (#9/#11/#29, 2d APT), book-month
(#9/#11/#29 after smoke). No second month. Existing completed outputs are
reused only after exact hash and full independent QC PASS. Unknown/failed
outputs are NOT retried or overwritten.

Writer cripta: writes only /data/cripta/research/runs/microstructure_firstmonth_batch_r158_20261023
and child outputs of the pinned published feature scripts in corresponding
separate /data/cripta/research/runs/... roots. Published source checkout READ.
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

REPO = pathlib.Path("/srv/cripta/source_checkout")
SOURCE = REPO / "research/microstructure/experiments"
DATA = pathlib.Path("/data/cripta/research/runs")
RUN_ROOT = DATA / "microstructure_firstmonth_batch_r158_20261023"
COINS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
GROUPS = {
    "price": {
        "root": DATA / "microstructure_firstmonth_price19_22_20261023",
        "mat": "r158_firstmonth_range_vwap_poc_v2.py",
        "qc": "r158_firstmonth_range_vwap_poc_independent_qc_v2.py",
        "feature": "features.jsonl.gz",
        "result_field": "file_sha256",
        "code_field": "program_sha256",
        "snap_mat": "materializer.py",
        "qc_status": "ALL_MINUTES_DECIMAL_QC_PASS_NOT_RESEARCH_READY",
        "qc_count": "rows",
    },
    "book": {
        "root": DATA / "microstructure_firstmonth_book9_11_29_20261023",
        "mat": "r158_firstmonth_book9_11_29_v2.py",
        "qc": "r158_firstmonth_book9_11_29_independent_qc_v2.py",
        "feature": "book_features.jsonl.gz",
        "result_field": "data_sha256",
        "code_field": "code_sha256",
        "snap_mat": "book_materializer.py",
        "qc_status": "ALL_BOOK_OBSERVED_MINUTES_DECIMAL_QC_PASS_NOT_RESEARCH_READY",
        "qc_count": None,
    },
}


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1048576), b""):
            h.update(b)
    return h.hexdigest()


def json_create(path, val):
    temp = path.with_name(path.name + ".part")
    with temp.open("x") as f:
        json.dump(val, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)


def available_memory_kib():
    mem = pathlib.Path("/proc/meminfo").read_text().splitlines()
    candidates = [int(s.split()[1]) for s in mem if s.startswith("MemAvailable:")]
    if not candidates:
        raise ValueError("NO_MEMORY_SIGNAL")
    return candidates[0]


def verify_existing(group, symbol, days):
    cfg = GROUPS[group]
    target = cfg["root"] / ("physical_" + str(days) + "d") / symbol
    if not target.exists():
        return "MISSING", None
    resultfile = target / "result.json"
    if not resultfile.is_file():
        raise ValueError("PARTIAL_RESULT_NO_RESULT_JSON_" + symbol)
    result = json.loads(resultfile.read_text())
    if result.get("days") != days:
        raise ValueError("SCOPE_MISMATCH_" + symbol)
    if group == "price":
        if result.get("symbols") != [symbol]:
            raise ValueError("MISSING_SYMBOL_ID")
    elif result.get("symbol") != symbol:
        raise ValueError("MISSING_SYMBOL_ID")
    output = target / cfg["feature"]
    source = target / "source_snapshot" / cfg["snap_mat"]
    if (
        not output.is_file() or not source.is_file()
        or sha(output) != result[cfg["result_field"]]
        or sha(source) != result[cfg["code_field"]]
    ):
        raise ValueError("PUBLISHED_INPUT_HASH_DRIFT_" + symbol)
    checker = target / "independent_qc_report.json"
    if not checker.is_file():
        return "PHYSICAL_ONLY", target
    report = json.loads(checker.read_text())
    if (
        report.get("status") != cfg["qc_status"]
        or report.get("symbol") != symbol
    ):
        raise ValueError("QC_RECEIPT_UNTRUSTED_" + symbol)
    if group == "price":
        if (
            report.get("days") != days or report.get("rows") != 1440 * days
            or report.get("original_sha256") != sha(output)
        ):
            raise ValueError("QC_PRICE_SCOPE_OR_HASH_DRIFT_" + symbol)
    else:
        if (
            report.get("scope_days") != days
            or report.get("totals", {}).get("rows") != 1440 * days
            or report.get("book_feature_sha256") != sha(output)
        ):
            raise ValueError("QC_BOOK_SCOPE_OR_HASH_DRIFT_" + symbol)
    return "QC_PASS", target


def execute(batch, code, title, args):
    filename = batch / "logs" / (title + ".stdout.log")
    errorfile = batch / "logs" / (title + ".stderr.log")
    cmd = [sys.executable, "-B", str(code), *args]
    start = time.monotonic()
    with filename.open("xb") as out, errorfile.open("xb") as err:
        run = subprocess.run(
            cmd, cwd=REPO, stdout=out, stderr=err,
            stdin=subprocess.DEVNULL, check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    report = {
        "name": title, "exit_code": run.returncode,
        "elapsed_seconds": round(time.monotonic() - start, 2),
        "stdout": str(filename), "stderr": str(errorfile),
    }
    print(json.dumps(report), flush=True)
    if run.returncode != 0:
        raise RuntimeError("FAILED_SOURCE_STAGE " + title + " exit=" + str(run.returncode))
    return report


def launch(stage):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("ACTOR_NOT_CRIPTA")
    if stage not in ("price-month", "book-smoke", "book-month"):
        raise ValueError("STAGE_NOT_AUTHORIZED")
    if not os.access(DATA, os.W_OK | os.X_OK):
        raise PermissionError("DATA_DISK_NOT_WRITABLE")
    if available_memory_kib() < 1400000:
        raise MemoryError("INSUFFICIENT_AVAILABLE_RAM_KIB")
    group, days = (
        ("price", 30) if stage == "price-month" else
        ("book", 2) if stage == "book-smoke" else ("book", 30)
    )
    symbols = ("APTUSDT",) if days == 2 else COINS
    if stage == "book-month":
        status, path = verify_existing("book", "APTUSDT", 2)
        if status != "QC_PASS" or path is None:
            raise ValueError("BOOK_BOUNDED_SMOKE_QC_REQUIRED")
    state = {
        s: verify_existing(group, s, days)[0] for s in symbols
    }
    if any(s not in ("MISSING", "PHYSICAL_ONLY", "QC_PASS") for s in state.values()):
        raise ValueError("BAD_EXISTING_STATE")
    run = RUN_ROOT / stage
    if run.exists():
        raise FileExistsError("NO_BATCH_RESTART_OR_OVERWRITE")
    os.umask(0o007)
    RUN_ROOT.mkdir(mode=0o2770, exist_ok=True)
    run.mkdir(mode=0o2770, exist_ok=False)
    for name in ("source_snapshot", "logs", "results"):
        (run / name).mkdir(mode=0o2770, exist_ok=False)
    script = pathlib.Path(__file__).resolve()
    shutil.copyfile(script, run / "source_snapshot" / "coordinator.py")
    cfg = GROUPS[group]
    hashes = {}
    for field in ("mat", "qc"):
        name = cfg[field]
        path = SOURCE / name
        snapshot = run / "source_snapshot" / name
        shutil.copyfile(path, snapshot)
        hashes[field] = sha(path)
    source_head = (REPO / ".git/refs/heads/main").read_text().strip()
    (run / "source_commit.txt").write_text(source_head + "\n")
    (run / "source_sha256.txt").write_text(sha(script) + "\n")
    (run / "command.txt").write_text(
        sys.executable + " -B " + str(script) + " --stage " + stage + "\n"
    )
    (run / "environment.txt").write_text(
        "uid=" + str(os.geteuid()) + "\npython=" + sys.executable
        + "\nplatform=" + platform.system() + "\n"
    )
    metadata = {
        "status": "PREPARED", "stage": stage, "group": group,
        "days": days, "coins": list(symbols), "actor_uid": os.geteuid(),
        "single_worker": True, "source_commit": source_head,
        "script_hashes": hashes, "prior_checked_state": state,
        "second_month_read": False, "runtime_effect": "NONE",
    }
    json_create(run / "input_provenance.json", metadata)
    json_create(run / "run_manifest.json", metadata)
    logs = []
    receipts = []
    try:
        for idx, symbol in enumerate(symbols):
            if (REPO / ".git/refs/heads/main").read_text().strip() != source_head:
                raise RuntimeError("SOURCE_COMPOSITION_DRIFT_DURING_JOB")
            present, target = verify_existing(group, symbol, days)
            if present == "MISSING":
                logs.append(execute(
                    run, run / "source_snapshot" / cfg["mat"],
                    symbol + "_physical",
                    ["--symbol", symbol, "--days", str(days)],
                ))
                present, target = verify_existing(group, symbol, days)
                if present != "PHYSICAL_ONLY":
                    raise ValueError("POST_MATERIALIZE_STATE_INVALID")
            if present == "PHYSICAL_ONLY":
                logs.append(execute(
                    run, run / "source_snapshot" / cfg["qc"],
                    symbol + "_independent_qc",
                    ["--symbol", symbol, "--days", str(days)],
                ))
                present, target = verify_existing(group, symbol, days)
                if present != "QC_PASS":
                    raise ValueError("POST_QC_STATE_INVALID")
            receipt = {
                "symbol": symbol, "status": present,
                "run_dir": str(target),
                "sha256_result": sha(target / "result.json"),
                "sha256_qc": sha(target / "independent_qc_report.json"),
            }
            json_create(run / "results" / (symbol + ".json"), receipt)
            receipts.append(receipt)
            print(json.dumps({
                "stage": stage, "symbol": symbol,
                "completed": idx + 1, "total": len(symbols),
                "remaining": len(symbols) - idx - 1,
                "result": "PASS", "heartbeat": True,
            }), flush=True)
    except Exception as error:
        json_create(run / "results" / "failure.json", {
            "status": "FAILED", "stage": stage,
            "error": type(error).__name__ + ": " + str(error),
            "completed": receipts,
        })
        raise
    result = {
        "status": "FIRST_MONTH_GROUP_FULL_ROW_QC_PASS_NOT_RESEARCH_READY",
        "stage": stage, "days": days, "actor_uid": os.geteuid(),
        "symbols": list(symbols),
        "source_commit": source_head, "script_hashes": hashes,
        "completed": receipts, "execution_logs": logs,
        "receipt_clock": "UNKNOWN", "second_month_access": False,
    }
    json_create(run / "result.json", result)
    json_create(run / "results" / "index.json", {
        "status": result["status"], "sha256_result": sha(run / "result.json"),
    })
    print(json.dumps({
        "status": result["status"], "stage": stage,
        "days": days, "coins": len(symbols), "run_dir": str(run),
    }), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument(
        "--stage", required=True,
        choices=("price-month", "book-smoke", "book-month"),
    )
    a = p.parse_args()
    launch(a.stage)
